"""Offline encrypted-code, native-login and session-cache contracts."""
import base64
import contextlib
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

from cryptography.fernet import Fernet

import glados_manual
from glados_auth import CookieStore, MailSettings, session_cache_key
from glados_browser import NativeBrowser
from glados_common import GladosError
from glados_manual import (CodeChallenge, GitHubReply, ManualPaths, REPLY_PATH,
                           encrypt_reply, identity)
from glados_runtime import RuntimePaths, project_path


class CacheKeyTests(unittest.TestCase):
    def setUp(self):
        self.environment = mock.patch.dict(os.environ, {"GLADOS_COOKIE_STORE_KEY": "",
            "GLADOS_GMAIL_ADDRESS": "", "GLADOS_GMAIL_APP_PASSWORD": "", "GLADOS_MANUAL_SESSION_CACHE": "1"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.accounts = [{"email": "alice@example.net", "cookie":
            "gld:sess=fixture-session; gld:sess.sig=0123456789abcdefghijklmnopqrstuvwxyzABCD"}]

    def test_optional_and_explicit_key(self):
        with mock.patch.dict(os.environ, {"GLADOS_MANUAL_SESSION_CACHE": "0"}):
            self.assertEqual(session_cache_key("invalid"), "")
        key = Fernet.generate_key().decode("ascii")
        with mock.patch.dict(os.environ, {"GLADOS_COOKIE_STORE_KEY": key}):
            self.assertEqual(session_cache_key("invalid"), key)
            self.assertIsNone(MailSettings.from_environment())

    def test_signed_secret_derivation_is_stable_and_label_independent(self):
        key = session_cache_key(json.dumps(self.accounts))
        changed = [{**self.accounts[0], "email": "bob@example.net"}]
        self.assertEqual(key, session_cache_key(json.dumps(changed, indent=4)))
        self.assertEqual(len(base64.urlsafe_b64decode(key)), 32)
        self.assertNotIn(self.accounts[0]["cookie"], key)
        Fernet(key)

    def test_public_labels_and_weak_or_missing_signatures_are_not_keys(self):
        for cookie in ("", "gld:sess=example", "gld:sess.sig=aaaaaaaaaaaaaaaaaaaaaaaaaa",
                       "gld:sess.sig=...", "gld:sess.sig=0123456789abcdefghijklmn\n"):
            with self.subTest(cookie=cookie), self.assertRaises(GladosError):
                session_cache_key(json.dumps([{"email": "public@example.net", "cookie": cookie}]))

    def test_signature_change_changes_key(self):
        original = session_cache_key(json.dumps(self.accounts))
        changed = [{**self.accounts[0], "cookie": self.accounts[0]["cookie"].replace("ABCD", "WXYZ")}]
        self.assertNotEqual(original, session_cache_key(json.dumps(changed)))


class EncryptedCodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.challenge = CodeChallenge("987654321", "1", 1)

    def test_only_bound_ciphertext_is_published(self):
        envelope = encrypt_reply(self.challenge.document, "123456")
        self.assertNotIn("code", envelope)
        self.assertNotIn("123456", json.dumps(envelope))
        self.assertNotIn("PRIVATE", json.dumps(self.challenge.document))
        self.assertEqual(self.challenge.decrypt(envelope), "123456")

    def test_reply_for_other_run_attempt_account_or_challenge_is_ignored(self):
        envelope = encrypt_reply(self.challenge.document, "123456")
        for field, value in (("run_id", "111111111"), ("attempt", "2"), ("account", 2),
                             ("challenge", "b" * 32)):
            with self.subTest(field=field):
                self.assertIsNone(self.challenge.decrypt({**envelope, field: value}))
        self.assertIsNone(self.challenge.decrypt({}))
        self.assertIsNone(self.challenge.decrypt(None))

    def test_encrypted_identity_cannot_be_relabelled(self):
        document = {**self.challenge.document, "account": 2}
        envelope = {**encrypt_reply(document, "123456"), "account": 1}
        with self.assertRaises(GladosError) as error:
            self.challenge.decrypt(envelope)
        self.assertNotIn("123456", str(error.exception))

    def test_invalid_ciphertext_and_codes_do_not_leak(self):
        envelope = encrypt_reply(self.challenge.document, "123456")
        with self.assertRaises(GladosError):
            self.challenge.decrypt({**envelope, "ciphertext": "invalid"})
        for value in ("", "12345", "1234567", "code123456", "12 3456"):
            with self.subTest(value=value), self.assertRaises(GladosError):
                encrypt_reply(self.challenge.document, value)

    def test_expired_or_invalid_public_challenge_is_rejected(self):
        for field, value in (("expires_at", time.time() - 1), ("expires_at", True),
                             ("public_key", "invalid"), ("account", True), ("run_id", "../escape")):
            with self.subTest(field=field, value=value), self.assertRaises(GladosError):
                encrypt_reply({**self.challenge.document, field: value}, "123456")


class ManualRuntimeTests(unittest.TestCase):
    def setUp(self):
        parent = project_path(".cache/panels/tmp")
        parent.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="test-manual-", dir=str(parent))
        self.addCleanup(self.directory.cleanup)
        self.runtime = RuntimePaths(self.directory.name)
        self.paths = ManualPaths("987654321", "1", self.runtime)

    def test_public_challenge_contains_no_private_key_or_code(self):
        def reply(timeout):
            document = glados_manual.read_json(self.paths.challenge)
            return encrypt_reply(document, "123456")
        relay = mock.Mock()
        relay.get.side_effect = reply
        with contextlib.redirect_stdout(io.StringIO()) as output:
            value = self.paths.await_code(1, relay)
        self.assertEqual(value, "123456")
        self.assertNotIn(value, output.getvalue())
        self.assertNotIn(value, self.paths.challenge.read_text())
        self.assertEqual(glados_manual.read_json(self.paths.state)["state"], "logging_in")
        self.assertFalse(list(self.paths.directory.glob(".manual-*.tmp")))

    def test_saved_session_is_encrypted_with_existing_secret_material(self):
        raw = json.dumps([{"email": "alice@example.net", "cookie":
            "gld:sess=fixture-session; gld:sess.sig=0123456789abcdefghijklmnopqrstuvwxyzABCD"}])
        with mock.patch.dict(os.environ, {"GLADOS_MANUAL_SESSION_CACHE": "1", "GLADOS_COOKIE_STORE_KEY": ""}):
            key = session_cache_key(raw)
        values = {"gld:sess": "new-private-fixture-session", "gld:sess.sig": "new-fixture-signature"}
        store = CookieStore(key, self.runtime)
        store.put("alice@example.net", "https://glados.cloud", values)
        for value in (values["gld:sess"], "alice@example.net", key):
            self.assertNotIn(value.encode(), store.path.read_bytes())
        self.assertEqual(CookieStore(key, self.runtime).get("alice@example.net", "https://glados.cloud"), values)

    def test_paths_and_action_identity_cannot_escape_project(self):
        for value in ("../escape", "", "name"):
            with self.subTest(value=value), self.assertRaises(GladosError):
                ManualPaths(value, "1", self.runtime)
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}), self.assertRaises(GladosError):
            glados_manual.action_paths()

    def test_completed_wait_reports_only_safe_state(self):
        self.paths.update("complete", success=True, account=1, category="free_reward", reward=2)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(glados_manual.wait(self.paths), 0)
        self.assertIn('"reward": 2', output.getvalue())


class RelayHttpTests(unittest.TestCase):
    def reply(self, envelope):
        raw = json.dumps(envelope).encode()
        return {"type": "file", "path": REPLY_PATH, "encoding": "base64", "size": len(raw),
                "content": base64.b64encode(raw).decode()}

    def test_repository_url_and_tls_are_constrained(self):
        relay = GitHubReply("owner/repo", "fixture-read-token")
        self.assertEqual(relay.url, "https://api.github.com/repos/owner/repo/contents/" + REPLY_PATH + "?ref=main")
        for repository in ("owner/repo?evil", "owner/repo/escape", "https://evil/repo"):
            with self.subTest(repository=repository), self.assertRaises(GladosError):
                GitHubReply(repository, "fixture-read-token")

    def test_only_expected_public_file_is_read(self):
        relay = GitHubReply("owner/repo", "fixture-read-token")
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(self.reply({})).encode()
        relay.opener = mock.Mock()
        relay.opener.open.return_value = response
        self.assertEqual(relay.get(3), {})
        self.assertEqual(relay.opener.open.call_args.kwargs["timeout"], 3)
        response.read.return_value = json.dumps({**self.reply({}), "path": "unapproved"}).encode()
        with self.assertRaises(GladosError) as error:
            relay.get(3)
        self.assertNotIn("fixture-read-token", str(error.exception))


class NativeManualLoginTests(unittest.TestCase):
    def setUp(self):
        self.browser = NativeBrowser(headless=True)
        self.browser.browser = mock.MagicMock()
        self.context = self.browser.browser.new_context.return_value
        self.page = self.context.new_page.return_value
        self.context.cookies.return_value = [{"name": "gld:sess", "value": "fixture", "domain": "glados.cloud"}]
        self.provider = mock.Mock(return_value="123456")
        self.page.evaluate.side_effect = [
            {"status": 200, "data": {"code": 0}},
            {"status": 200, "data": {"code": 0}},
            {"status": 200, "data": {"code": 0, "data": {"email": "alice@example.net"}}}]

    def test_authorization_login_and_identity_use_the_actual_browser(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            reply = self.browser.login_with_code("https://glados.cloud", "alice@example.net", self.provider)
        self.provider.assert_called_once_with()
        calls = self.page.evaluate.call_args_list
        self.assertEqual([call.args[1]["path"] for call in calls],
                         ["/api/authorization", "/api/login", "/api/user/status"])
        self.assertEqual(calls[1].args[1]["payload"]["mailcode"], "123456")
        self.assertIn("AbortSignal.timeout(15000)", calls[0].args[0])
        self.assertNotIn("123456", output.getvalue())
        self.assertEqual(reply.payload["code"], 0)
        self.context.close.assert_called_once()
        self.context.request.post.assert_not_called()

    def test_refused_authorization_does_not_wait_for_or_submit_a_code(self):
        self.page.evaluate.side_effect = [{"status": 200, "data": {"code": 0, "captcha_required": True}}]
        with self.assertRaises(GladosError):
            self.browser.login_with_code("https://glados.cloud", "alice@example.net", self.provider)
        self.provider.assert_not_called()
        self.assertEqual(self.page.evaluate.call_count, 1)

    def test_rejected_login_and_wrong_identity_are_not_retried(self):
        for final in ({"code": 4}, {"code": 0, "data": {"email": "bob@example.net"}}):
            self.page.evaluate.reset_mock()
            self.provider.reset_mock()
            self.page.evaluate.side_effect = [{"status": 200, "data": {"code": 0}},
                {"status": 200, "data": final if final["code"] == 4 else {"code": 0}},
                {"status": 200, "data": final}]
            with self.subTest(final=final), self.assertRaises(GladosError):
                self.browser.login_with_code("https://glados.cloud", "alice@example.net", self.provider)
            self.provider.assert_called_once()
            self.assertLessEqual(self.page.evaluate.call_count, 3)

    def test_unarmed_business_writes_are_blocked(self):
        self.browser.login_with_code("https://glados.cloud", "alice@example.net", self.provider)
        guard = self.context.route.call_args.args[1]
        route = mock.Mock()
        route.request.method = "POST"
        route.request.url = "https://glados.cloud/api/user/checkin"
        guard(route)
        route.abort.assert_called_once()
        route.continue_.assert_not_called()


@unittest.skipUnless(os.getenv("GLADOS_BROWSER_TESTS") == "1", "opt-in real Chrome/local fixture")
class NativeLoginFixtureTests(unittest.TestCase):
    def test_real_chrome_fetch_receives_cookie_and_checks_identity(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def reply(self, value, cookies=False):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                if cookies:
                    self.send_header("Set-Cookie", "gld:sess=native-fixture-session; Path=/; HttpOnly")
                    self.send_header("Set-Cookie", "gld:sess.sig=native-fixture-signature; Path=/; HttpOnly")
                self.end_headers()
                self.wfile.write(json.dumps(value).encode())

            def do_GET(self):
                if self.path == "/api/user/status":
                    logged_in = "gld:sess=native-fixture-session" in self.headers.get("Cookie", "")
                    self.reply({"code": 0, "data": {"email": "alice@example.net"}} if logged_in else {"code": -2})
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<html><body>Native login fixture</body></html>")

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((self.path, payload, self.headers.get("User-Agent")))
                if self.path == "/api/authorization" and payload == {
                        "address": "alice@example.net", "site": "glados.network"}:
                    self.reply({"code": 0})
                elif self.path == "/api/login" and payload == {
                        "method": "email", "site": "glados.network", "email": "alice@example.net", "mailcode": "123456"}:
                    self.reply({"code": 0}, cookies=True)
                else:
                    self.reply({"code": 4})

        parent = project_path(".cache/panels/tmp")
        parent.mkdir(parents=True, exist_ok=True)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="login-", dir=str(parent)) as directory:
                origin = "http://127.0.0.1:" + str(server.server_address[1])
                runtime = RuntimePaths(directory)
                headless = os.getenv("GLADOS_BROWSER_TEST_HEADLESS", "1") != "0"
                with NativeBrowser(runtime=runtime, headless=headless, origins=(origin,)) as browser:
                    reply = browser.login_with_code(origin, "alice@example.net", lambda: "123456")
                    ua = browser.user_agent
                self.assertEqual(reply.payload["code"], 0)
                self.assertEqual({value["name"] for value in reply.cookies}, {"gld:sess", "gld:sess.sig"})
                self.assertEqual([value[0] for value in requests], ["/api/authorization", "/api/login"])
                self.assertTrue(all(value[2] == ua for value in requests))
                self.assertFalse(list(runtime.tmp.glob("chrome-*")))
                self.assertFalse((runtime.base / "browser.lock").exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
