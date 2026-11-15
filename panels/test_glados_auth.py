"""Offline recovery, encrypted-cache and runtime-boundary regressions."""
from datetime import datetime, timezone
from email.utils import format_datetime
import json
import os
from pathlib import Path
import ssl
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import glados_auth
import glados_test_support
import test_checkin
from glados_auth import CookieStore, EmailRecovery, GmailInbox, MailSettings, verification_code
from glados_common import GladosError, auth_denied, email_address, turnstile_denied
from glados_runtime import PROJECT_ROOT, RunLock, RuntimePaths, project_path
from glados_test_support import native_fixture_runtime

checkin = test_checkin.checkin


class RuntimeTestCase(unittest.TestCase):
    def setUp(self):
        parent = project_path(".cache/panels/tmp")
        parent.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="test-runtime-", dir=str(parent))
        self.addCleanup(self.directory.cleanup)
        self.paths = RuntimePaths(self.directory.name)

    def test_default_and_relative_paths_are_project_bound(self):
        self.assertEqual(project_path(".cache/panels"), PROJECT_ROOT / ".cache/panels")
        for value in (PROJECT_ROOT.parent / "glados-auto-checkin-other", PROJECT_ROOT / "../escape"):
            with self.subTest(value=value), self.assertRaises(GladosError):
                project_path(value)

    def test_posix_browser_fixture_uses_short_project_tmp(self):
        with mock.patch.object(glados_test_support, "os", SimpleNamespace(name="posix")):
            runtime = native_fixture_runtime(self.directory.name)
        self.assertEqual(runtime.base, self.paths.base)
        self.assertEqual(runtime.tmp, project_path(".cache/panels/tmp"))
        self.assertNotEqual(runtime.tmp, runtime.base / "tmp")
        for name in ("TEMP", "TMP", "TMPDIR"):
            self.assertEqual(runtime.child_environment()[name], str(runtime.tmp))
        self.assertEqual(RunLock(runtime).path, runtime.base / "browser.lock")
        for path in (runtime.home, runtime.cache, runtime.config, runtime.state):
            path.relative_to(runtime.base)

    def test_windows_browser_fixture_keeps_isolated_tmp(self):
        with mock.patch.object(glados_test_support, "os", SimpleNamespace(name="nt")):
            runtime = native_fixture_runtime(self.directory.name)
        self.assertEqual(runtime.tmp, runtime.base / "tmp")
        self.assertEqual(runtime.child_environment()["TEMP"], str(runtime.tmp))

    def test_shared_fixture_tmp_preserves_other_owned_profiles(self):
        with mock.patch.object(glados_test_support, "os", SimpleNamespace(name="posix")):
            first = native_fixture_runtime(self.paths.base / "first")
            second = native_fixture_runtime(self.paths.base / "second")
        with RunLock(first), RunLock(second):
            first_profile = first.new_profile()
            self.addCleanup(first.remove_profile, first_profile)
            second_profile = second.new_profile()
            self.addCleanup(second.remove_profile, second_profile)
            self.assertEqual(first_profile.parent, second_profile.parent)
            self.assertNotEqual(first_profile, second_profile)
            first.remove_profile(first_profile)
            self.assertFalse(first_profile.exists())
            self.assertTrue(second_profile.exists())

    def test_driver_environment_is_restored_without_external_outputs(self):
        self.paths.ensure()
        with mock.patch.dict(os.environ, {"TEMP": "unapproved", "SSLKEYLOGFILE": "unapproved"}):
            with self.paths.driver_environment():
                self.assertEqual(os.environ["TEMP"], str(self.paths.tmp))
                self.assertNotIn("SSLKEYLOGFILE", os.environ)
                for name in ("HOME", "APPDATA", "LOCALAPPDATA", "XDG_CACHE_HOME", "PLAYWRIGHT_BROWSERS_PATH"):
                    Path(os.environ[name]).relative_to(PROJECT_ROOT)
            self.assertEqual(os.environ["TEMP"], "unapproved")
            self.assertEqual(os.environ["SSLKEYLOGFILE"], "unapproved")
        self.assertNotIn("SSLKEYLOGFILE", self.paths.child_environment())

    @unittest.skipUnless(os.name == "nt", "Windows known-folder contract")
    def test_windows_known_appdata_folder_resolves_inside_runtime(self):
        import ctypes
        self.paths.ensure()
        with self.paths.driver_environment():
            for folder, expected in ((28, self.paths.local), (26, self.paths.roaming)):
                value = ctypes.create_unicode_buffer(260)
                result = ctypes.windll.shell32.SHGetFolderPathW(None, folder, None, 0, value)
                self.assertEqual(result, 0)
                self.assertEqual(Path(value.value).resolve(), expected)

    def test_only_a_new_owned_profile_is_removed(self):
        profile = self.paths.new_profile()
        (profile / "data").write_text("fixture", encoding="ascii")
        self.paths.remove_profile(profile)
        self.assertFalse(profile.exists())
        with self.assertRaises(GladosError):
            self.paths.remove_profile(self.paths.base)

    def test_lock_is_exclusive_and_released(self):
        with RunLock(self.paths):
            with self.assertRaises(GladosError):
                RunLock(self.paths).__enter__()
        with RunLock(self.paths):
            pass
        self.assertFalse((self.paths.base / "browser.lock").exists())

    def test_changed_lock_is_not_removed(self):
        lock = RunLock(self.paths).__enter__()
        lock.path.write_text(json.dumps({"owner": "another-run"}), encoding="ascii")
        lock.__exit__(None, None, None)
        self.assertTrue(lock.path.exists())

    def test_encrypted_cache_round_trip_and_origin_binding(self):
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode("ascii")
        values = {"gld:sess": "fixture-private-session", "gld:sess.sig": "fixture-signature"}
        store = CookieStore(key, self.paths)
        store.put("alice@example.net", "https://glados.cloud", values)
        raw = store.path.read_bytes()
        for secret in (b"alice@example.net", b"fixture-private-session", key.encode()):
            self.assertNotIn(secret, raw)
        restored = CookieStore(key, self.paths)
        self.assertEqual(restored.get("alice@example.net", "https://glados.cloud"), values)
        self.assertEqual(restored.get("alice@example.net", "https://glados.rocks"), {})
        self.assertEqual(restored.get("bob@example.net", "https://glados.cloud"), {})
        self.assertFalse(list(self.paths.base.glob(".sessions-*.tmp")))
        restored.remove("alice@example.net")
        self.assertEqual(CookieStore(key, self.paths).entries, {})

    def test_invalid_key_and_incomplete_session_are_rejected(self):
        with self.assertRaises(GladosError) as error:
            CookieStore("private-invalid-key", self.paths)
        self.assertNotIn("private-invalid-key", str(error.exception))
        from cryptography.fernet import Fernet
        store = CookieStore(Fernet.generate_key().decode(), self.paths)
        with self.assertRaises(GladosError):
            store.put("alice@example.net", "https://glados.cloud", {"gld:sess": "missing-signature"})


class EmailParsingTestCase(unittest.TestCase):
    def setUp(self):
        self.requested = datetime(2026, 11, 15, 0, 0, tzinfo=timezone.utc).timestamp()

    def message(self, *, to="alice@example.net", sender="noreply@glados.network", seconds=0,
                text="Your access code is 123456", extra="", html=False):
        date = format_datetime(datetime.fromtimestamp(self.requested + seconds, timezone.utc))
        return (f"From: {sender}\r\nTo: {to}\r\nDate: {date}\r\nSubject: GLaDOS access code\r\n"
                f"{extra}Content-Type: text/{'html' if html else 'plain'}; charset=utf-8\r\n\r\n{text}").encode()

    def parse(self, message):
        return verification_code(message, "alice@example.net", self.requested, ("glados.network",))

    def test_plain_and_html_codes(self):
        self.assertEqual(self.parse(self.message()), "123456")
        self.assertEqual(self.parse(self.message(html=True, text="<style>999999</style><p>Code <b>123456</b></p>")), "123456")

    def test_forwarding_matches_original_recipient_not_unified_inbox(self):
        self.assertIsNone(self.parse(self.message(to="unified@example.net", extra="Delivered-To: alice@example.net\r\n")))
        self.assertEqual(self.parse(self.message(to="unified@example.net", extra="X-Original-To: alice@example.net\r\n")), "123456")

    def test_wrong_sender_recipient_stale_and_ambiguous_codes_are_rejected(self):
        messages = (self.message(to="bob@example.net"), self.message(sender="noreply@glados.network.attacker.net"),
                    self.message(seconds=-31), self.message(seconds=661), self.message(text="Codes 123456 and 654321"),
                    self.message(text="code 1234567"))
        for value in messages:
            with self.subTest(value=value[:30]):
                self.assertIsNone(self.parse(value))

    def test_turnstile_does_not_trigger_auth_recovery(self):
        for payload in ({"code": 5, "reason": "turnstile"},
                        {"code": -2, "message": "Cloudflare verification failed"}):
            self.assertTrue(turnstile_denied(payload))
            self.assertFalse(auth_denied(payload))
        self.assertTrue(auth_denied({"code": 4, "reason": "device-mismatch"}))
        self.assertFalse(auth_denied({"code": True}))

    def test_mail_settings_are_optional_complete_and_redacted(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(MailSettings.from_environment())
            os.environ["GLADOS_GMAIL_ADDRESS"] = "unified@example.net"
            with self.assertRaises(GladosError):
                MailSettings.from_environment()
            os.environ.update({"GLADOS_GMAIL_APP_PASSWORD": "privatepassword", "GLADOS_COOKIE_STORE_KEY": "privatekey"})
            settings = MailSettings.from_environment()
            self.assertNotIn("privatepassword", repr(settings))
            self.assertNotIn("privatekey", repr(settings))
        with self.assertRaises(GladosError):
            email_address("display label")


class GmailInboxTestCase(unittest.TestCase):
    setUp = EmailParsingTestCase.setUp
    message = EmailParsingTestCase.message

    def inbox(self, uids=b"9 10", codes=("123456",)):
        connection = mock.MagicMock()
        connection.select.return_value = ("OK", [])
        connection.status.return_value = ("OK", [b'"INBOX" (UIDNEXT 10 UIDVALIDITY 4)'])
        payloads = [self.message(text="Code " + code) for code in codes]
        def request(operation, *args):
            if operation == "search":
                return "OK", [uids]
            return "OK", [(b"message", payloads[min(int(args[0]) - 10, len(payloads) - 1)])]
        connection.uid.side_effect = request
        patcher = mock.patch("glados_auth.imaplib.IMAP4_SSL", return_value=connection)
        patcher.start()
        self.addCleanup(patcher.stop)
        return GmailInbox(MailSettings("unified@example.net", "fixture-password", "fixture-key")), connection

    def test_only_new_uids_are_read_with_peek(self):
        inbox, connection = self.inbox()
        with inbox:
            self.assertEqual(inbox.wait_for_code("alice@example.net", self.requested), "123456")
        connection.select.assert_called_once_with("INBOX", readonly=True)
        calls = connection.uid.call_args_list
        self.assertNotIn(mock.call("fetch", "9", "(BODY.PEEK[])"), calls)
        self.assertIn(mock.call("fetch", "10", "(BODY.PEEK[])"), calls)
        connection.logout.assert_called_once()

    def test_multiple_new_codes_stop_without_guessing(self):
        inbox, _ = self.inbox(b"10 11", ("123456", "654321"))
        with inbox, self.assertRaises(GladosError):
            inbox.wait_for_code("alice@example.net", self.requested)

    def test_uid_validity_reset_stops_before_fetch(self):
        inbox, connection = self.inbox()
        with inbox:
            connection.status.return_value = ("OK", [b'"INBOX" (UIDNEXT 10 UIDVALIDITY 8)'])
            with self.assertRaises(GladosError):
                inbox.wait_for_code("alice@example.net", self.requested)
        connection.uid.assert_not_called()

    def test_mail_tls_keeps_certificate_checks_without_environment_key_logs(self):
        inbox, _ = self.inbox()
        with mock.patch.dict(os.environ, {"SSLKEYLOGFILE": "unapproved-key-log"}), \
                mock.patch("glados_auth.ssl.create_default_context", side_effect=AssertionError("implicit key log")):
            with inbox:
                pass
        context = glados_auth.imaplib.IMAP4_SSL.call_args.kwargs["ssl_context"]
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertIsNone(context.keylog_filename)


class RecoveryTestCase(unittest.TestCase):
    setUp = test_checkin.RequestRetryTestCase.setUp
    response = test_checkin.RequestRetryTestCase.response

    def recovery(self):
        settings = MailSettings("unified@example.net", "fixture-password", "fixture-key")
        self.store = mock.Mock()
        self.store.get.return_value = {}
        return EmailRecovery(settings, self.store)

    def client(self):
        return checkin.AccountClient(self.panel, self.cookie, browser_user_agent=checkin.DEFAULT_USER_AGENT,
                                     allow_empty_cookie=True)

    def test_valid_configured_session_needs_no_email(self):
        recovery = self.recovery()
        self.session.get.return_value = self.response(data={"code": 0, "data": {"email": "alice@example.net"}})
        with self.client() as client, mock.patch.object(recovery, "login") as login:
            self.assertFalse(recovery.prepare(client, "alice@example.net"))
        login.assert_not_called()
        self.store.put.assert_called_once()

    def test_network_html_and_wrong_identity_do_not_send_codes(self):
        for response in (self.response(503), self.response(403), self.response(data={"code": 0, "data": {"email": "bob@example.net"}})):
            with self.subTest(status=response.status_code):
                recovery = self.recovery()
                self.session.get.return_value = response
                with self.client() as client, mock.patch.object(recovery, "login") as login:
                    with self.assertRaises(GladosError):
                        recovery.prepare(client, "alice@example.net")
                    login.assert_not_called()

    def test_explicit_auth_rejection_recovers_once(self):
        recovery = self.recovery()
        self.session.get.return_value = self.response(data={"code": -2})
        with self.client() as client, mock.patch.object(recovery, "login") as login:
            self.assertTrue(recovery.prepare(client, "alice@example.net"))
            login.assert_called_once_with(client, "alice@example.net")

    def test_revoked_cookies_do_not_hide_explicit_auth_rejection(self):
        recovery = self.recovery()
        revoked = "gld:sess=; Max-Age=0; Path=/, gld:sess.sig=; Max-Age=0; Path=/"
        self.session.get.return_value = self.response(data={"code": -2}, headers={"Set-Cookie": revoked})
        with self.client() as client, mock.patch.object(recovery, "login") as login:
            self.assertTrue(recovery.prepare(client, "alice@example.net"))
        login.assert_called_once()

    def test_wrong_cached_identity_never_sends_an_email(self):
        recovery = self.recovery()
        self.store.get.return_value = {"gld:sess": "other-account", "gld:sess.sig": "other-signature"}
        self.session.get.return_value = self.response(data={"code": 0, "data": {"email": "bob@example.net"}})
        with self.client() as client, mock.patch.object(recovery, "login") as login, self.assertRaises(GladosError) as error:
            recovery.prepare(client, "alice@example.net")
        self.assertEqual(error.exception.kind, "identity")
        login.assert_not_called()

    def test_login_protocol_and_readonly_mailbox(self):
        recovery = self.recovery()
        fresh = "gld:sess=fresh-session; Path=/, gld:sess.sig=fresh-signature; Path=/"
        self.session.post.side_effect = [self.response(data={"code": 0}), self.response(data={"code": 0}, headers={"Set-Cookie": fresh})]
        self.session.get.return_value = self.response(data={"code": 0, "data": {"email": "alice@example.net"}})
        inbox = mock.MagicMock()
        inbox.__enter__.return_value.wait_for_code.return_value = "123456"
        with self.client() as client, mock.patch("glados_auth.GmailInbox", return_value=inbox):
            recovery.login(client, "alice@example.net")
            self.assertEqual(client.cookie_values()["gld:sess"], "fresh-session")
        self.assertEqual(self.session.post.call_count, 2)
        self.assertEqual(self.session.post.call_args.kwargs["json"], {
            "method": "email", "site": "glados.network", "email": "alice@example.net", "mailcode": "123456"})
        self.assertNotIn("123456", str(self.print.call_args_list))


if __name__ == "__main__":
    unittest.main()
