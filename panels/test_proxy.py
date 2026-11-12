"""Optional GLaDOS proxy isolation and failure behavior."""
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import test_checkin

checkin = test_checkin.checkin


class ProxyTestCase(unittest.TestCase):
    setUp = test_checkin.RequestRetryTestCase.setUp
    response = test_checkin.RequestRetryTestCase.response
    successful_responses = test_checkin.AccountClientTestCase.successful_responses

    def test_proxy_is_disabled_by_default_without_changing_session_creation(self):
        self.successful_responses()
        result = checkin.process_account(self.panel, {"cookie": self.cookie})
        self.assertTrue(result.success)
        self.create.assert_called_once_with("chrome124")
        for call in self.session.get.call_args_list + self.session.post.call_args_list:
            self.assertNotIn("proxy", call.kwargs)

    def test_all_glados_requests_use_the_same_proxy_including_challenge(self):
        proxy = "socks5h://127.0.0.1:40000"
        panel = dict(checkin.PANELS[0], fallback_origins=())
        self.session.post.return_value = self.response()
        self.session.get.side_effect = [
            self.response(data={"code": 0, "nonce": "test-nonce", "ttl": 300000}),
            self.response(data={"code": 0, "data": {"leftDays": 3}}),
            self.response(data={"code": 0, "points": 19}),
        ]
        with mock.patch.dict(os.environ, {"GLADOS_PROXY_URL": proxy}):
            result = checkin.process_account(panel, {"cookie": self.cookie})
        self.assertTrue(result.success)
        calls = self.session.get.call_args_list + self.session.post.call_args_list
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(call.kwargs["proxy"] == proxy for call in calls))
        self.create.assert_called_once_with("chrome124", trust_env=False)
        self.sleep.assert_called_once_with(2.7)

    def test_glados_proxy_does_not_affect_railgun(self):
        self.successful_responses()
        panel = next(panel for panel in checkin.PANELS if panel["name"] == "Railgun")
        with mock.patch.dict(os.environ, {"GLADOS_PROXY_URL": "socks5h://127.0.0.1:40000"}):
            result = checkin.process_account(panel, {"cookie": self.cookie})
        self.assertTrue(result.success)
        for call in self.session.get.call_args_list + self.session.post.call_args_list:
            self.assertNotIn("proxy", call.kwargs)

    def test_invalid_proxy_is_rejected_before_any_network_session(self):
        values = ("missing-scheme", "file:///private/path", "http://", "http://host:70000",
                  "http://host:wrong", "http://host/path", "http://host?secret=value", "http://host\r\nprivate",
                  "http://proxy host:40000", "http://user:raw password@host:40000")
        for value in values:
            with self.subTest(case=value[:12]), mock.patch.dict(os.environ, {"GLADOS_PROXY_URL": value}):
                result = checkin.process_account(self.panel, {"cookie": self.cookie})
                self.assertFalse(result.success)
                self.assertNotIn(value, result.message)
        self.create.assert_not_called()

    def test_supported_proxy_schemes_and_encoded_credentials_are_accepted(self):
        values = ("http://localhost:40000", "https://proxy.example:443/", "socks5://[::1]:40000",
                  "socks5h://127.0.0.1:40000", "http://user:p%40ss@127.0.0.1:40000")
        for value in values:
            with self.subTest(scheme=value.split(":", 1)[0]), mock.patch.dict(os.environ, {"GLADOS_PROXY_URL": value}):
                self.assertEqual(checkin.configured_proxy(self.panel), value)

    def test_proxy_credentials_are_not_logged(self):
        self.successful_responses()
        proxy = "http://private-proxy-user:private-proxy-password@127.0.0.1:40000"
        with mock.patch.dict(os.environ, {"GLADOS_PROXY_URL": proxy}):
            result = checkin.process_account(self.panel, {"cookie": self.cookie})
        self.assertTrue(result.success)
        for value in (proxy, "private-proxy-user", "private-proxy-password"):
            self.assertNotIn(value, result.message + str(self.print.call_args_list))

    def test_proxy_failure_never_retries_without_the_proxy(self):
        proxy = "socks5h://private-user:private-password@127.0.0.1:40000"
        self.session.get.side_effect = ConnectionError(proxy)
        panel = dict(checkin.PANELS[0], checkin_challenge=False)
        with mock.patch.dict(os.environ, {"GLADOS_PROXY_URL": proxy}):
            with self.assertRaises(checkin.PanelRequestError):
                checkin.request_json(panel, self.cookie, "/api/user/status")
        self.assertEqual(self.session.get.call_count, 9)
        self.assertTrue(all(call.kwargs["proxy"] == proxy for call in self.session.get.call_args_list))
        self.assertNotIn("private-password", str(self.print.call_args_list))

    def test_proxy_diagnostic_stays_status_only(self):
        self.session.get.return_value = self.response(data={"code": 0, "data": {"leftDays": 3}})
        with mock.patch.dict(os.environ, {"GLADOS_PROXY_URL": "socks5h://127.0.0.1:40000"}):
            result = checkin.process_account(self.panel, {"cookie": self.cookie}, diagnostic=True)
        self.assertTrue(result.success)
        self.assertIsNone(result.checkin_outcome)
        self.session.get.assert_called_once()
        self.session.post.assert_not_called()
        self.assertEqual(self.session.get.call_args.kwargs["proxy"], "socks5h://127.0.0.1:40000")


@unittest.skipIf(isinstance(checkin.cffi_requests, mock.Mock), "curl_cffi is not installed")
class WireProxyTestCase(unittest.TestCase):
    def test_explicit_proxy_overrides_no_proxy_and_never_falls_back_on_failure(self):
        for proxy_status in (200, 502):
            with self.subTest(proxy_status=proxy_status):
                captured = {"origin": [], "proxy": []}

                def handler(source, status):
                    class Handler(BaseHTTPRequestHandler):
                        def do_GET(self):
                            captured[source].append(self.path)
                            body = json.dumps({"code": 0}).encode("utf-8")
                            self.send_response(status)
                            self.send_header("Content-Type", "application/json")
                            self.send_header("Content-Length", str(len(body)))
                            self.end_headers()
                            self.wfile.write(body)

                        def log_message(self, *args):
                            pass

                    return Handler

                origin = ThreadingHTTPServer(("127.0.0.1", 0), handler("origin", 200))
                proxy = ThreadingHTTPServer(("127.0.0.1", 0), handler("proxy", proxy_status))
                servers = (origin, proxy)
                threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers]
                for thread in threads:
                    thread.start()
                origin_url = f"http://127.0.0.1:{origin.server_port}"
                panel = dict(checkin.PANELS[0], origin=origin_url, fallback_origins=())
                env = {name: "" for name in ("GLADOS_USER_AGENT", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                                             "http_proxy", "https_proxy", "all_proxy")}
                env.update({"GLADOS_PROXY_URL": f"http://127.0.0.1:{proxy.server_port}",
                            "NO_PROXY": "*", "no_proxy": "*"})
                try:
                    with mock.patch.dict(os.environ, env), checkin.AccountClient(panel, "dummy=session") as client:
                        if proxy_status == 200:
                            self.assertEqual(checkin.request_json_once(panel, "dummy=session", "/", "GET", None, client),
                                             {"code": 0})
                        else:
                            with self.assertRaises(checkin.PanelRequestError):
                                checkin.request_json_once(panel, "dummy=session", "/", "GET", None, client)
                    self.assertEqual(captured["origin"], [])
                    self.assertEqual(captured["proxy"], [origin_url + "/"])
                finally:
                    for server, thread in zip(servers, threads):
                        server.shutdown()
                        server.server_close()
                        thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
