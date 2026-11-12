"""UA normalization and browser request-header regressions."""
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import test_checkin

checkin = test_checkin.checkin


class HeaderTestCase(unittest.TestCase):
    setUp = test_checkin.RequestRetryTestCase.setUp

    def test_common_copy_paste_wrappers_are_removed(self):
        ua = checkin.DEFAULT_USER_AGENT
        values = (ua, "  " + ua + "  ", "User-Agent: " + ua, '"' + ua + '"',
                  "'" + ua + "'", 'User-Agent: "' + ua + '"', '"User-Agent: ' + ua + '"')
        for value in values:
            with self.subTest(wrapper=value[:16]):
                self.assertEqual(checkin.normalize_user_agent(value), ua)
                with checkin.AccountClient(self.panel, self.cookie, value) as client:
                    self.assertEqual(client.headers("/api/user/status")["User-Agent"], ua)
                    self.assertEqual(client.ua_source, "account")

    def test_invalid_wrappers_and_controls_fail_before_session_creation(self):
        values = ('"', '""', "User-Agent:", "User-Agent: User-Agent: Chrome/124.0",
                  '""Chrome/124.0""', '"Chrome/124.0', "Chrome/124.0\r\nInjected: x",
                  "Chrome/124.0\t", "Chrome/124.0\x00", "Chrome/124.0 \u201c")
        for value in values:
            with self.subTest(case=value[:16]):
                result = checkin.process_account(self.panel, {"cookie": self.cookie, "user_agent": value})
                self.assertFalse(result.success)
        self.create.assert_not_called()

    def test_single_quoted_product_token_is_not_rewritten(self):
        self.assertEqual(checkin.normalize_user_agent("'cli-token'"), "'cli-token'")

    def test_configured_ua_source_and_normalization_are_visible_without_value(self):
        ua = checkin.DEFAULT_USER_AGENT
        with mock.patch.dict(os.environ, {"GLADOS_USER_AGENT": "User-Agent: " + ua}):
            with checkin.AccountClient(self.panel, self.cookie) as client:
                self.assertEqual(client.ua_source, "panel")
                self.assertTrue(client.ua_normalized)
            with checkin.AccountClient(self.panel, self.cookie, "custom-account-browser") as client:
                self.assertEqual(client.ua_source, "account")
        with checkin.AccountClient(self.panel, self.cookie) as client:
            self.assertEqual(client.ua_source, "default")

    def test_valid_account_override_does_not_validate_unused_panel_ua(self):
        with mock.patch.dict(os.environ, {"GLADOS_USER_AGENT": '"broken'}):
            with checkin.AccountClient(self.panel, self.cookie, checkin.DEFAULT_USER_AGENT) as client:
                self.assertEqual(client.ua_source, "account")

    def test_browser_fetch_headers_are_explicit_for_both_panels(self):
        for panel in checkin.PANELS:
            headers = checkin.generate_headers(self.cookie, panel)
            self.assertEqual(headers["Accept-Language"], "zh-CN,zh;q=0.9,en;q=0.8")
            self.assertEqual(headers["Sec-Fetch-Dest"], "empty")
            self.assertEqual(headers["Sec-Fetch-Mode"], "cors")
            self.assertEqual(headers["Sec-Fetch-Site"], "same-origin")
            self.assertEqual(headers["Origin"], panel["origin"])
            self.assertEqual(headers["Referer"], panel["origin"] + "/console/checkin")

    def test_diagnostic_logs_only_safe_ua_metadata(self):
        ua = "custom-private-browser"
        self.session.get.return_value = test_checkin.RequestRetryTestCase.response(
            self, data={"code": 0, "data": {"leftDays": 3}})
        result = checkin.process_account(self.panel, {"cookie": self.cookie, "user_agent": ua}, diagnostic=True)
        self.assertTrue(result.success)
        self.assertNotIn(ua, str(self.print.call_args_list))
        self.assertNotIn("private-session", str(self.print.call_args_list))
        self.session.post.assert_not_called()


@unittest.skipIf(isinstance(checkin.cffi_requests, mock.Mock), "curl_cffi is not installed")
class WireHeaderTestCase(unittest.TestCase):
    def test_real_curl_sends_explicit_headers_to_loopback_only(self):
        captured = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                captured.update(dict(self.headers.items()))
                body = json.dumps({"code": 0}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        panel = dict(checkin.PANELS[0], origin=f"http://127.0.0.1:{server.server_port}", fallback_origins=())
        env = {name: "" for name in ("GLADOS_PROXY_URL", "GLADOS_USER_AGENT", "HTTP_PROXY", "HTTPS_PROXY",
                                     "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")}
        env.update({"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
        try:
            with mock.patch.dict(os.environ, env), checkin.AccountClient(panel, "dummy=session") as client:
                self.assertEqual(checkin.request_json_once(panel, "dummy=session", "/", "GET", None, client), {"code": 0})
            headers = {key.lower(): value for key, value in captured.items()}
            self.assertEqual(headers["accept-language"], "zh-CN,zh;q=0.9,en;q=0.8")
            self.assertEqual(headers["sec-fetch-mode"], "cors")
            self.assertEqual(headers["sec-fetch-site"], "same-origin")
            self.assertEqual(headers["user-agent"], checkin.DEFAULT_USER_AGENT)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
