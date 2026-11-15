"""Browser protocol unit tests and opt-in real Chrome/local HTTP fixture tests."""
from contextlib import nullcontext
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock
from urllib.parse import urlsplit

from glados_browser import NativeBrowser, proxy_options
from glados_common import CHALLENGE_SUFFIX, GladosError
from glados_runtime import RuntimePaths, project_path


class Response:
    def __init__(self, data, status=200):
        self.data, self.status = data, status

    def json(self):
        return self.data


class FakePage:
    def __init__(self, context):
        self.context = context
        self.frames = []
        self.main_frame = object()
        self.events = {}
        self.requests = []
        self.ready = True
        self.emit_response = True
        self.transport_failure = False
        self.bad_proof = False
        self.finished = Response({"code": 0, "message": "Checkin! Got 3 Points"})

    def on(self, event, callback):
        self.events[event] = callback

    def expect_event(self, *args, **kwargs):
        return nullcontext(SimpleNamespace(value=SimpleNamespace(response=lambda:
            self.context.challenge_response or Response(self.context.challenge))))

    def goto(self, *args, **kwargs):
        pass

    def locator(self, selector):
        return self

    def wait_for(self, *args, **kwargs):
        pass

    def evaluate(self, expression):
        return self.ready

    def wait_for_timeout(self, *args):
        pass

    def click(self, *args, **kwargs):
        nonce = self.context.challenge["nonce"]
        body = {"token": "glados.cloud", "nonce": nonce,
                "proof": "invalid" if self.bad_proof else hashlib.sha256((nonce + CHALLENGE_SUFFIX).encode()).hexdigest(),
                "tsState": "ok", "turnstile": "fixture-token"}
        for _ in range(2):
            request = SimpleNamespace(url="https://glados.cloud/api/user/checkin", method="POST", post_data_json=body,
                is_navigation_request=lambda: False, response=lambda: self.finished)
            route = mock.Mock(request=request)
            self.context.guard(route)
            if route.continue_.called:
                self.requests.append(request)
                if self.transport_failure:
                    self.events["requestfailed"](request)
                elif self.emit_response:
                    self.events["requestfinished"](request)


class FakeContext:
    def __init__(self):
        self.page = FakePage(self)
        self.challenge = {"code": 0, "nonce": "fixture-nonce", "ttl": 300000, "turnstile": "fixture-sitekey"}
        self.challenge_response = None
        self.request = mock.Mock()
        self.request.get.return_value = Response({"code": 0, "data": {"email": "alice@example.net"}})
        self.close = mock.Mock()
        self.guard = None

    def set_default_timeout(self, *args):
        pass

    def add_cookies(self, cookies):
        self.injected = cookies

    def new_page(self):
        return self.page

    def route(self, pattern, guard):
        self.guard = guard

    def cookies(self, url):
        return []


class BrowserProtocolTestCase(unittest.TestCase):
    def setUp(self):
        self.context = FakeContext()
        self.browser = NativeBrowser(headless=True)
        self.browser.browser = mock.Mock()
        self.browser.browser.new_context.return_value = self.context
        self.browser.user_agent = "fixture-UA"
        self.browser.timeout = 5000
        counter = iter(range(100, 10000))
        patcher = mock.patch("glados_browser.time.monotonic", side_effect=lambda: next(counter) / 10)
        patcher.start()
        self.addCleanup(patcher.stop)

    def check(self, **kwargs):
        return self.browser.run("https://glados.cloud", [], expected_email="alice@example.net", **kwargs)

    def test_proxy_credentials_are_not_command_line_material(self):
        options = proxy_options("http://name:p%40ss@example.net:1234")
        self.assertEqual(options["server"], "http://example.net:1234")
        self.assertEqual(options["password"], "p@ss")
        self.assertEqual(proxy_options("socks5h://[::1]:1080")["server"], "socks5://[::1]:1080")
        for value in ("socks5://private:secret@host:1080", "ftp://host", "http://host/path", "http://host?key=private",
                      "http://host:0", "http://host:65536", "http://private:p%0Ass@host"):
            with self.assertRaises(GladosError) as error:
                proxy_options(value)
            self.assertNotIn("secret", str(error.exception))

    def test_exact_verification_payload_and_only_one_post(self):
        reply = self.check()
        self.assertEqual(reply.payload["code"], 0)
        self.assertEqual(len(self.context.page.requests), 1)
        self.context.close.assert_called_once()
        self.assertNotIn("user_agent", self.browser.browser.new_context.call_args.kwargs)
        self.assertEqual(self.context.request.get.call_args.kwargs["headers"]["User-Agent"], "fixture-UA")

    def test_invalid_proof_is_blocked_before_submission(self):
        self.context.page.bad_proof = True
        with self.assertRaises(GladosError) as error:
            self.check()
        self.assertFalse(error.exception.submitted)
        self.assertEqual(self.context.page.requests, [])

    def test_unknown_response_has_no_fallback_or_post_replay(self):
        self.context.page.emit_response = False
        with self.assertRaises(GladosError) as error:
            self.check()
        self.assertTrue(error.exception.submitted)
        self.assertFalse(error.exception.fallback)
        self.assertEqual(len(self.context.page.requests), 1)

    def test_not_ready_and_readonly_verification_never_submit(self):
        self.context.page.ready = False
        with self.assertRaises(GladosError) as error:
            self.check()
        self.assertEqual(error.exception.kind, "turnstile")
        self.assertEqual(self.context.page.requests, [])
        self.context.page.ready = True
        result = self.check(verify_only=True)
        self.assertTrue(result.payload["ready"])
        self.assertEqual(self.context.page.requests, [])

    def test_wrong_account_and_unapproved_origin_stop_before_page_navigation(self):
        self.context.request.get.return_value = Response({"code": 0, "data": {"email": "bob@example.net"}})
        with self.assertRaises(GladosError) as error:
            self.check()
        self.assertEqual(error.exception.kind, "identity")
        with self.assertRaises(GladosError):
            self.browser.run("https://attacker.invalid", [])
        self.assertEqual(self.context.page.requests, [])

    def test_auth_rejection_is_returned_without_post_or_guessing(self):
        self.context.request.get.return_value = Response({"code": -2}, 401)
        self.assertEqual(self.check().payload["code"], -2)
        self.assertEqual(self.context.page.requests, [])

    def test_invalid_challenge_is_not_downgraded(self):
        for value in (None, False, "bad", 0, float("inf")):
            with self.subTest(value=value):
                self.context.challenge["ttl"] = value
                with self.assertRaises(GladosError) as error:
                    self.check()
                self.assertEqual(error.exception.kind, "challenge")
                self.assertFalse(error.exception.fallback)
        self.assertEqual(self.context.page.requests, [])

    def test_non_object_status_never_falls_back_or_submits(self):
        for payload in (None, [], "private-upstream", False, {"code": 0, "data": []}):
            with self.subTest(payload=payload):
                self.context.request.get.return_value = Response(payload)
                with self.assertRaises(GladosError) as error:
                    self.check()
                self.assertFalse(error.exception.fallback)
                self.assertFalse(error.exception.submitted)
                self.assertNotIn("private-upstream", str(error.exception))
        self.assertEqual(self.context.page.requests, [])

    def test_bad_nonce_and_challenge_body_are_not_downgraded(self):
        for nonce in (None, "", " " * 10, "x" * 4097, "\ud800"):
            with self.subTest(nonce=repr(nonce)[:20]):
                self.context.challenge["nonce"] = nonce
                with self.assertRaises(GladosError) as error:
                    self.check()
                self.assertEqual(error.exception.kind, "challenge")
                self.assertFalse(error.exception.fallback)
        for response in (Response([], 200), Response(self.context.challenge, 503),
                         SimpleNamespace(status=200, json=mock.Mock(side_effect=ValueError("private-body")))):
            with self.subTest(response=response):
                self.context.challenge_response = response
                with self.assertRaises(GladosError) as error:
                    self.check()
                self.assertEqual(error.exception.kind, "challenge")
                self.assertFalse(error.exception.fallback)
                self.assertNotIn("private-body", str(error.exception))
        self.assertEqual(self.context.page.requests, [])

    def test_expired_challenge_is_not_submitted(self):
        self.context.challenge["ttl"] = 2800
        with mock.patch("glados_browser.time.monotonic", side_effect=(0, 0, 0, 3, 3.1)), \
                self.assertRaises(GladosError) as error:
            self.check()
        self.assertEqual(error.exception.kind, "challenge")
        self.assertFalse(error.exception.submitted)
        self.assertEqual(self.context.page.requests, [])

    def test_transport_failure_after_submission_is_not_replayed(self):
        self.context.page.transport_failure = True
        with self.assertRaises(GladosError) as error:
            self.check()
        self.assertTrue(error.exception.submitted)
        self.assertFalse(error.exception.fallback)
        self.assertEqual(len(self.context.page.requests), 1)

    def test_invalid_post_response_is_unknown_and_not_replayed(self):
        self.context.page.finished = Response(["private-response"])
        with self.assertRaises(GladosError) as error:
            self.check()
        self.assertTrue(error.exception.submitted)
        self.assertFalse(error.exception.fallback)
        self.assertNotIn("private-response", str(error.exception))
        self.assertEqual(len(self.context.page.requests), 1)

    def test_explicit_post_403_denials_are_classified_without_replay(self):
        for payload in ({"code": 4, "reason": "device-mismatch"}, {"code": 5, "reason": "turnstile"}):
            with self.subTest(payload=payload):
                self.context.page.requests.clear()
                self.context.page.finished = Response(payload, 403)
                self.assertEqual(self.check().payload, payload)
                self.assertEqual(len(self.context.page.requests), 1)

    def test_context_cleanup_preserves_pending_failure_and_submission_state(self):
        self.context.close.side_effect = RuntimeError("private-profile")
        self.context.page.emit_response = False
        with self.assertRaises(GladosError) as error:
            self.check()
        self.assertTrue(error.exception.submitted)
        self.assertFalse(error.exception.fallback)
        self.assertNotIn("private-profile", str(error.exception))
        self.context.page.emit_response = True
        with self.assertRaises(GladosError) as error:
            self.check()
        self.assertTrue(error.exception.submitted)
        self.assertIn("cleanup failed", str(error.exception))


class BrowserLifecycleTestCase(unittest.TestCase):
    def test_startup_cleanup_failure_does_not_mask_original_error(self):
        browser = NativeBrowser(runtime=mock.MagicMock(), headless=True)
        failure = GladosError("Native browser unavailable", kind="config")
        with mock.patch("playwright.sync_api.sync_playwright"), mock.patch("glados_browser.RunLock"), \
                mock.patch("glados_browser._chrome_executable", side_effect=failure), \
                mock.patch.object(browser, "close", side_effect=GladosError("cleanup failure")), \
                self.assertRaises(GladosError) as error:
            browser.__enter__()
        self.assertIs(error.exception, failure)

    def test_chrome_termination_timeout_is_killed_before_cleanup(self):
        browser = NativeBrowser(runtime=mock.Mock(), headless=True)
        browser.process = process = mock.Mock()
        browser.profile, browser.lock = "fixture-profile", mock.MagicMock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("fixture", 5), 0]
        browser.close()
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        browser.paths.remove_profile.assert_called_once_with("fixture-profile")
        self.assertIsNone(browser.process)
        self.assertIsNone(browser.lock)

    def test_unstoppable_chrome_retains_profile_and_lock(self):
        browser = NativeBrowser(runtime=mock.Mock(), headless=True)
        browser.process = process = mock.Mock()
        browser.profile, browser.lock = "fixture-profile", mock.MagicMock()
        process.poll.return_value = None
        process.terminate.side_effect = OSError("private-process")
        with self.assertRaises(GladosError) as error:
            browser.close()
        self.assertEqual(error.exception.kind, "runtime")
        self.assertNotIn("private-process", str(error.exception))
        browser.paths.remove_profile.assert_not_called()
        browser.lock.__exit__.assert_not_called()


HTML = """<!doctype html><meta charset=utf-8><button class=ckb>Check in</button><script>
fetch('/api/user/checkin/challenge').then(r=>r.json()).then(challenge=>{
 window.turnstile={getResponse:()=> 'fixture-token'};
 document.querySelector('button').onclick=async()=>{
  const bytes=await crypto.subtle.digest('SHA-256', new TextEncoder().encode(challenge.nonce+'.glados-observe-v1'));
  const proof=[...new Uint8Array(bytes)].map(n=>n.toString(16).padStart(2,'0')).join('');
  const body={token:location.hostname,nonce:challenge.nonce,proof,tsState:'ok',turnstile:'fixture-token'};
  await fetch('/api/user/exchange',{method:'POST'}).catch(()=>{});
  await fetch('/api/user/checkin',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  fetch('/api/user/checkin',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).catch(()=>{});
 };
});</script>"""


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def account(self):
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == "gld:sess":
                return value
        return "anonymous"

    def send(self, content, kind="application/json"):
        data = content.encode() if isinstance(content, str) else json.dumps(content).encode()
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        account = self.account()
        if self.path == "/api/user/status":
            self.send({"code": 0, "data": {"email": account + "@example.net", "leftDays": 10}})
        elif self.path == "/api/user/checkin/challenge":
            self.send({"code": 0, "nonce": "fixture-" + account, "ttl": 300000, "turnstile": "fixture-sitekey"})
        else:
            self.send(HTML, "text/html")

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.posts.append((self.path, self.account(), raw))
        self.send({"code": 0, "message": "Checkin! Got 3 Points"})


@unittest.skipUnless(os.getenv("GLADOS_BROWSER_TESTS") == "1", "opt-in native Chrome/local fixture test")
class NativeFixtureTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parent = project_path(".cache/panels/tmp")
        parent.mkdir(parents=True, exist_ok=True)
        cls.directory = tempfile.TemporaryDirectory(prefix="test-native-", dir=str(parent))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.server.posts = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = "http://127.0.0.1:" + str(cls.server.server_port)
        cls.environment = mock.patch.dict(os.environ, {"CI": "true", "GLADOS_BROWSER_CHANNEL": "chrome"})
        cls.environment.start()
        cls.browser = NativeBrowser(runtime=RuntimePaths(cls.directory.name), headless=True, origins=(cls.origin,))
        try:
            cls.browser.__enter__()
        except Exception:
            cls.environment.stop()
            cls.server.shutdown()
            cls.server.server_close()
            cls.directory.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
        finally:
            cls.environment.stop()
            cls.server.shutdown()
            cls.server.server_close()
            cls.thread.join(timeout=5)
            cls.directory.cleanup()

    def setUp(self):
        self.server.posts.clear()

    def cookies(self, account):
        return [{"name": name, "value": value, "url": self.origin + "/", "httpOnly": True}
                for name, value in (("gld:sess", account), ("gld:sess.sig", "fixture-signature"))]

    def test_native_context_performs_one_verified_post_and_blocks_other_writes(self):
        result = self.browser.run(self.origin, self.cookies("alice"), expected_email="alice@example.net")
        self.assertEqual(result.payload["message"], "Checkin! Got 3 Points")
        self.assertEqual([(path, account) for path, account, _ in self.server.posts], [("/api/user/checkin", "alice")])
        body = json.loads(self.server.posts[0][2])
        self.assertEqual(body["tsState"], "ok")
        self.assertEqual(body["turnstile"], "fixture-token")

    def test_second_context_has_no_first_account_session(self):
        for account in ("alice", "bob"):
            self.browser.run(self.origin, self.cookies(account), expected_email=account + "@example.net")
        self.assertEqual([account for _, account, _ in self.server.posts], ["alice", "bob"])

    def test_readonly_native_verification_has_no_application_post(self):
        result = self.browser.run(self.origin, self.cookies("alice"), expected_email="alice@example.net", verify_only=True)
        self.assertTrue(result.payload["ready"])
        self.assertEqual(self.server.posts, [])

    def test_identity_mismatch_never_reaches_signup(self):
        with self.assertRaises(GladosError):
            self.browser.run(self.origin, self.cookies("alice"), expected_email="bob@example.net")
        self.assertEqual(self.server.posts, [])


if __name__ == "__main__":
    unittest.main()
