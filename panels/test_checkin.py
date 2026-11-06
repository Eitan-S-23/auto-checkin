# -- coding: utf-8 --
"""panels/checkin.py（GLaDOS 系面板签到）单元测试。

curl_cffi 未安装时，本地测试用桩模块注入 sys.modules 后再导入被测模块；
统一通知模块的 send 替换为桩，验证内容组装与推送调用。
"""
import json
import sys
import unittest
from typing import Literal
from unittest import mock

# 已安装时使用真实模块，但所有网络请求仍由测试替身接管。
try:
    import curl_cffi
except ModuleNotFoundError as error:
    if error.name != "curl_cffi":
        raise
    sys.modules["curl_cffi"] = mock.MagicMock()

import checkin


class ParseAccountsTestCase(unittest.TestCase):
    def test_标准_json_数组(self):
        raw = json.dumps([{"email": "a@b.c", "cookie": "x"}])
        self.assertEqual(checkin.parse_accounts(raw), [{"email": "a@b.c", "cookie": "x"}])

    def test_双重编码字符串(self):
        raw = json.dumps(json.dumps([{"email": "a@b.c", "cookie": "x"}]))
        self.assertEqual(checkin.parse_accounts(raw), [{"email": "a@b.c", "cookie": "x"}])

    def test_元素为_json_字符串(self):
        raw = json.dumps([json.dumps({"email": "a@b.c", "cookie": "x"})])
        self.assertEqual(checkin.parse_accounts(raw), [{"email": "a@b.c", "cookie": "x"}])

    def test_outer_structure_must_be_array(self):
        for raw in ('{}', 'null', '42'):
            with self.assertRaises(ValueError):
                checkin.parse_accounts(raw)

    def test_bad_entry_does_not_discard_valid_following_entries(self):
        raw = json.dumps(["bad-json", {"cookie": "session=value"}])
        self.assertEqual(checkin.parse_accounts(raw), ["bad-json", {"cookie": "session=value"}])


class TranslateMessageTestCase(unittest.TestCase):
    def test_签到成功提取积分(self):
        self.assertIn("3", checkin.translate_message("Checkin! Got 3 Points"))

    def test_已知状态翻译(self):
        cases = {
            "Please Try Tomorrow": "明天再试",
            "Checkin Repeats! Please Try Tomorrow": "重复签到",
            "Today's observation logged. Return tomorrow for more points.": "已签到",
            "Already checked in today. Come back tomorrow.": "已签到",
        }
        for raw, expected in cases.items():
            self.assertIn(expected, checkin.translate_message(raw))

    def test_未知状态(self):
        self.assertIn("未知", checkin.translate_message("Something new"))


class CookieHeaderCompatibilityTestCase(unittest.TestCase):
    def setUp(self):
        keys = {panel["ua_env"]: "" for panel in checkin.PANELS}
        patcher = mock.patch.dict("os.environ", keys, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_current_session_names_are_forwarded_unchanged(self):
        cookie = "gld:sess=opaque-session-v2; gld:sess.sig=opaque-signature"
        for panel in checkin.PANELS:
            with self.subTest(panel=panel["name"]):
                self.assertEqual(checkin.generate_headers(cookie, panel)["Cookie"], cookie)

    def test_legacy_session_names_are_not_renamed(self):
        cookie = "koa:sess=legacy-session; koa:sess.sig=legacy-signature"
        headers = checkin.generate_headers(cookie, checkin.PANELS[0])
        self.assertEqual(headers["Cookie"], cookie)
        self.assertNotIn("gld:", headers["Cookie"])

    def test_opaque_cookie_values_are_not_decoded_or_rewritten(self):
        cookie = "gld:sess=opaque.v2_a-b+/==; gld:sess.sig=signed-value%2B%3D"
        headers = checkin.generate_headers(cookie, checkin.PANELS[0])
        self.assertEqual(headers["Cookie"], cookie)

    def test_additional_cookie_fields_are_preserved(self):
        cookie = "theme=light; gld:sess=session; gld:sess.sig=signature; preference=en"
        self.assertEqual(checkin.generate_headers(cookie, checkin.PANELS[0])["Cookie"], cookie)

    def test_panel_user_agent_overrides_are_independent(self):
        values = {"GLADOS_USER_AGENT": "glados-browser", "RAILGUN_USER_AGENT": "railgun-browser"}
        with mock.patch.dict("os.environ", values):
            for panel in checkin.PANELS:
                headers = checkin.generate_headers("session=value", panel)
                self.assertEqual(headers["User-Agent"], values[panel["ua_env"]])
                self.assertEqual(headers["Origin"], panel["origin"])

    def test_sign_sends_current_cookie_and_matching_panel_token(self):
        cookie = "gld:sess=opaque-session; gld:sess.sig=opaque-signature"
        for panel in checkin.PANELS:
            with self.subTest(panel=panel["name"]), mock.patch("checkin.create_retry_session") as create:
                session = create.return_value
                session.post.return_value.status_code = 200
                session.post.return_value.json.return_value = {"message": "Checkin! Got 3 Points"}
                self.assertIn("3", checkin.sign(panel, cookie))
                session.post.assert_called_once()
                args, kwargs = session.post.call_args
                self.assertEqual(args, (panel["origin"] + "/api/user/checkin",))
                self.assertEqual(kwargs["headers"]["Cookie"], cookie)
                self.assertEqual(kwargs["json"], {"token": panel["token"]})


class RequestRetryTestCase(unittest.TestCase):
    def setUp(self):
        patchers = [
            mock.patch.dict("os.environ", {"PANEL_MAX_ATTEMPTS": "3", "PANEL_RETRY_DELAY_SECONDS": "10"}),
            mock.patch("checkin.create_retry_session"),
            mock.patch("checkin.time.sleep"),
            mock.patch("builtins.print"),
        ]
        handles = []
        for patcher in patchers:
            handles.append(patcher.start())
            self.addCleanup(patcher.stop)
        _, self.create, self.sleep, self.print = handles
        self.session = self.create.return_value
        self.panel = checkin.PANELS[0]
        self.cookie = "gld:sess=private-session; gld:sess.sig=private-signature"

    def response(self, status=200, data=None, headers=None):
        response = mock.Mock(status_code=status, headers=headers or {})
        response.json.return_value = data if data is not None else {"code": 0, "message": "Checkin! Got 3 Points"}
        return response

    def test_success_is_not_retried_and_session_is_closed(self):
        response = self.response()
        self.session.get.return_value = response
        self.assertEqual(checkin.request_json(self.panel, self.cookie, "/api/user/status"), response.json.return_value)
        self.session.get.assert_called_once()
        self.session.close.assert_called_once()
        self.sleep.assert_not_called()
        self.assertFalse(self.session.get.call_args.kwargs["allow_redirects"])

    def test_retryable_http_status_recovers(self):
        for status in sorted(checkin.RETRYABLE_HTTP_STATUS):
            with self.subTest(status=status):
                self.session.get.reset_mock()
                self.sleep.reset_mock()
                rejected, success = self.response(status), self.response()
                self.session.get.side_effect = [rejected, success]
                result = checkin.request_json(self.panel, self.cookie, "/api/user/status")
                self.assertEqual(result, success.json.return_value)
                self.assertEqual(self.session.get.call_count, 2)
                rejected.json.assert_not_called()
                self.sleep.assert_called_once_with(10)

    def test_timeout_then_duplicate_checkin_is_normal(self):
        duplicate = "Checkin Repeats! Please Try Tomorrow"
        self.session.post.side_effect = [TimeoutError(self.cookie), self.response(data={"code": 1, "message": duplicate})]
        self.assertEqual(checkin.sign(self.panel, self.cookie), checkin.translate_message(duplicate))
        self.assertEqual(self.session.post.call_count, 2)
        self.sleep.assert_called_once_with(10)

    def test_curl_transport_error_retries_without_echoing_credentials(self):
        error = RuntimeError(self.cookie)
        error.code = 28
        self.session.get.side_effect = [error, self.response()]
        checkin.request_json(self.panel, self.cookie, "/api/user/status")
        self.assertEqual(self.session.get.call_count, 2)
        self.assertNotIn("private-session", str(self.print.call_args_list))
        self.assertNotIn("private-signature", str(self.print.call_args_list))

    def test_connection_failure_retries(self):
        self.session.get.side_effect = [ConnectionError(self.cookie), self.response()]
        checkin.request_json(self.panel, self.cookie, "/api/user/status")
        self.assertEqual(self.session.get.call_count, 2)

    def test_certificate_and_configuration_errors_are_not_retried(self):
        for code in (3, 60, 67):
            with self.subTest(curl_code=code):
                error = RuntimeError(self.cookie)
                error.code = code
                self.session.get.reset_mock()
                self.session.get.side_effect = error
                with self.assertRaises(checkin.PanelRequestError) as caught:
                    checkin.request_json(self.panel, self.cookie, "/api/user/status")
                self.session.get.assert_called_once()
                self.sleep.assert_not_called()
                self.assertNotIn("private-session", str(caught.exception))

    def test_non_retryable_http_errors_are_not_retried(self):
        for status in (302, 400, 401, 403, 404, 501):
            with self.subTest(status=status):
                self.session.get.reset_mock()
                response = self.response(status)
                self.session.get.return_value = response
                with self.assertRaises(checkin.PanelRequestError):
                    checkin.request_json(self.panel, self.cookie, "/api/user/status")
                self.session.get.assert_called_once()
                response.json.assert_not_called()
                self.sleep.assert_not_called()

    def test_auth_and_device_denials_override_success_and_are_not_retried(self):
        cases = [
            {"code": -2, "message": "没有权限 " + self.cookie},
            {"code": "-2", "message": "permission denied"},
            {"code": 0, "reason": "device-mismatch", "message": "Checkin! Got 3 Points"},
            {"code": 4, "message": "Automated check-in detected " + self.cookie},
        ]
        for data in cases:
            with self.subTest(data=data):
                self.session.get.reset_mock()
                self.session.get.return_value = self.response(data=data)
                with self.assertRaises(checkin.PanelRequestError) as caught:
                    checkin.request_json(self.panel, self.cookie, "/api/user/status")
                self.session.get.assert_called_once()
                self.sleep.assert_not_called()
                self.assertNotIn("private-session", str(caught.exception))

    def test_unknown_business_response_is_not_retried(self):
        data = {"code": 9, "message": "Try later"}
        self.session.post.return_value = self.response(data=data)
        self.assertEqual(checkin.request_json(self.panel, self.cookie, "/api/user/checkin", "POST"), data)
        self.session.post.assert_called_once()
        self.sleep.assert_not_called()

    def test_observation_response_is_not_retried(self):
        message = "Today's observation logged. Return tomorrow for more points."
        self.session.post.return_value = self.response(data={"code": 1, "message": message})
        self.assertEqual(checkin.sign(self.panel, self.cookie), checkin.translate_message(message))
        self.session.post.assert_called_once()
        self.sleep.assert_not_called()

    def test_malformed_json_is_not_replayed(self):
        for data in ([], None):
            with self.subTest(response=data):
                self.session.get.reset_mock()
                response = self.response(data=data)
                if data is None:
                    response.json.side_effect = ValueError(self.cookie)
                self.session.get.return_value = response
                with self.assertRaises(checkin.PanelRequestError) as caught:
                    checkin.request_json(self.panel, self.cookie, "/api/user/status")
                self.session.get.assert_called_once()
                self.sleep.assert_not_called()
                self.assertNotIn("private-session", str(caught.exception))

    def test_retry_after_seconds_is_respected(self):
        self.session.get.side_effect = [self.response(429, headers={"Retry-After": "25"}), self.response()]
        checkin.request_json(self.panel, self.cookie, "/api/user/status")
        self.sleep.assert_called_once_with(25)

    def test_retry_after_http_date_is_respected(self):
        self.session.get.side_effect = [
            self.response(503, headers={"Retry-After": "Thu, 01 Jan 1970 00:17:05 GMT"}), self.response(),
        ]
        with mock.patch("checkin.time.time", return_value=1000):
            checkin.request_json(self.panel, self.cookie, "/api/user/status")
        self.sleep.assert_called_once_with(25)

    def test_long_retry_after_stops_instead_of_retrying_early(self):
        self.session.get.return_value = self.response(429, headers={"Retry-After": "300"})
        with self.assertRaises(checkin.PanelRequestError):
            checkin.request_json(self.panel, self.cookie, "/api/user/status")
        self.session.get.assert_called_once()
        self.sleep.assert_not_called()

    def test_retry_after_invalid_and_past_values(self):
        for value in (None, "", "invalid", "-1"):
            self.assertIsNone(checkin.retry_after_seconds(value))
        with mock.patch("checkin.time.time", return_value=1000):
            self.assertEqual(checkin.retry_after_seconds("Thu, 01 Jan 1970 00:00:00 GMT"), 0)

    def test_exhaustion_is_bounded_and_reports_unknown_status(self):
        self.session.post.return_value = self.response(503)
        result = checkin.sign(self.panel, self.cookie)
        self.assertIn("状态未知", result)
        self.assertEqual(self.session.post.call_count, 3)
        self.session.close.assert_called_once()
        self.assertEqual(self.sleep.call_args_list, [mock.call(10), mock.call(20)])
        self.assertNotIn("private-session", result + str(self.print.call_args_list))

    def test_every_attempt_preserves_identity(self):
        self.session.post.side_effect = [self.response(503), self.response()]
        checkin.sign(self.panel, self.cookie)
        for call in self.session.post.call_args_list:
            self.assertEqual(call.args[0], self.panel["origin"] + "/api/user/checkin")
            self.assertEqual(call.kwargs["headers"]["Cookie"], self.cookie)
            self.assertEqual(call.kwargs["json"], {"token": self.panel["token"]})
        self.create.assert_called_once()

    def test_retry_can_be_disabled(self):
        self.session.get.return_value = self.response(503)
        with mock.patch.dict("os.environ", {"PANEL_MAX_ATTEMPTS": "1"}):
            with self.assertRaises(checkin.PanelRequestError):
                checkin.request_json(self.panel, self.cookie, "/api/user/status")
        self.session.get.assert_called_once()
        self.sleep.assert_not_called()

    def test_settings_fall_back_to_defaults(self):
        for attempts, delay in (("", ""), ("invalid", "invalid"), ("0", "-1"), ("6", "61")):
            with mock.patch.dict("os.environ", {"PANEL_MAX_ATTEMPTS": attempts, "PANEL_RETRY_DELAY_SECONDS": delay}):
                self.assertEqual(checkin.retry_settings(), (3, 10))
        with mock.patch.dict("os.environ", {"PANEL_MAX_ATTEMPTS": "5", "PANEL_RETRY_DELAY_SECONDS": "0"}):
            self.assertEqual(checkin.retry_settings(), (5, 0))

    def test_backoff_is_capped(self):
        self.session.get.side_effect = [self.response(503)] * 4 + [self.response()]
        with mock.patch.dict("os.environ", {"PANEL_MAX_ATTEMPTS": "5", "PANEL_RETRY_DELAY_SECONDS": "60"}):
            checkin.request_json(self.panel, self.cookie, "/api/user/status")
        self.assertEqual(self.sleep.call_args_list, [mock.call(60)] * 4)

    def test_status_and_points_share_retry_policy(self):
        self.session.get.side_effect = [
            self.response(503), self.response(data={"code": 0, "data": {"leftDays": "3", "traffic": 0}}),
            self.response(502), self.response(data={"code": 0, "points": "19"}),
        ]
        self.assertEqual(checkin.check_account_status(self.panel, self.cookie), ("3", "0.000"))
        self.assertEqual(checkin.fetch_points(self.panel, self.cookie), "19")
        self.assertEqual(self.session.get.call_count, 4)

    def test_failed_points_are_not_reported_as_zero(self):
        self.session.get.return_value = self.response(data={"code": -2})
        self.assertEqual(checkin.fetch_points(self.panel, self.cookie), "获取失败")
        self.session.get.assert_called_once()
        self.sleep.assert_not_called()

    def test_real_zero_points_and_missing_points_are_distinct(self):
        self.session.get.side_effect = [self.response(data={"points": 0}), self.response(data={"code": 0})]
        self.assertEqual(checkin.fetch_points(self.panel, self.cookie), "0")
        self.assertEqual(checkin.fetch_points(self.panel, self.cookie), "获取失败")
        self.sleep.assert_not_called()

    def test_failed_account_does_not_block_next_account(self):
        second_cookie = "gld:sess=second-session; gld:sess.sig=second-signature"
        accounts = [{"email": "first", "cookie": self.cookie}, {"email": "second", "cookie": second_cookie}]
        self.session.post.side_effect = [TimeoutError(self.cookie)] * 3 + [self.response()]
        status = self.response(data={"code": 0, "data": {"leftDays": "3", "traffic": 0}})
        points = self.response(data={"code": 0, "points": "19"})
        self.session.get.side_effect = [status, points]
        with mock.patch.dict("os.environ", {"GLADOS_ACCOUNTS_JSON": json.dumps(accounts)}), \
                mock.patch("checkin.random.randint", return_value=2):
            result = checkin.checkin_panel(self.panel)
            _, content = result
        self.assertFalse(result.success)
        self.assertIn("状态未知", content)
        self.assertIn("签到成功", content)
        self.assertIn("second", content)
        self.assertEqual(self.session.post.call_count, 4)
        self.assertEqual(self.session.post.call_args.kwargs["headers"]["Cookie"], second_cookie)
        self.assertEqual(self.session.get.call_count, 2)
        for call in self.session.get.call_args_list:
            self.assertEqual(call.kwargs["headers"]["Cookie"], second_cookie)
        self.assertEqual(self.sleep.call_args_list, [mock.call(10), mock.call(20), mock.call(2)])

    def test_session_is_reused_through_backoff_and_closed_at_end(self):
        self.session.get.side_effect = [self.response(503), self.response()]
        self.sleep.side_effect = lambda _: self.session.close.assert_not_called()
        checkin.request_json(self.panel, self.cookie, "/api/user/status")
        self.session.close.assert_called_once()


class BrowserProfileTestCase(unittest.TestCase):
    def setUp(self):
        profiles = Literal["chrome124", "chrome130", "chrome131_android", "edge101", "firefox133", "safari180"]
        patcher = mock.patch.object(checkin.cffi_requests, "BrowserTypeLiteral", profiles, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_default_ua_and_preset_are_consistent(self):
        self.assertIn("Chrome/124.", checkin.DEFAULT_USER_AGENT)
        self.assertEqual(checkin.browser_profile(checkin.DEFAULT_USER_AGENT), ("chrome124", True))

    def test_nearest_preset_does_not_rewrite_ua(self):
        self.assertEqual(checkin.browser_profile("Chrome/128.0"), ("chrome130", False))
        self.assertEqual(checkin.browser_profile("Android Chrome/131.0 Mobile"), ("chrome131_android", True))
        self.assertEqual(checkin.browser_profile("Chrome/101.0 Edg/101.0"), ("edge101", True))
        self.assertEqual(checkin.browser_profile("Firefox/133.0"), ("firefox133", True))
        self.assertEqual(checkin.browser_profile("Version/18.5 Safari/605.1"), ("safari180", False))
        self.assertEqual(checkin.browser_profile("custom-browser"), ("chrome124", False))

    def test_explicit_profile_is_validated(self):
        self.assertEqual(checkin.browser_profile("custom-browser", "chrome130"), ("chrome130", False))
        with self.assertRaises(checkin.PanelRequestError):
            checkin.browser_profile("custom-browser", "unsupported")

    def test_account_ua_overrides_panel_ua_and_default(self):
        panel = checkin.PANELS[0]
        with mock.patch.dict("os.environ", {"GLADOS_USER_AGENT": "Chrome/130.0"}), \
                mock.patch("checkin.create_retry_session") as create:
            with checkin.AccountClient(panel, "session=value", "Android Chrome/131.0 Mobile") as client:
                headers = client.headers("/api/user/status")
                self.assertEqual(client.profile, "chrome131_android")
                self.assertEqual(headers["User-Agent"], "Android Chrome/131.0 Mobile")
                self.assertEqual(headers["Sec-CH-UA-Mobile"], "?1")
                self.assertEqual(headers["Sec-CH-UA-Platform"], '"Android"')
                self.assertIn('v="131"', headers["Sec-CH-UA"])
            create.assert_called_once_with("chrome131_android")
            with checkin.AccountClient(panel, "session=value") as client:
                self.assertEqual(client.user_agent, "Chrome/130.0")
        with mock.patch.dict("os.environ", {"GLADOS_USER_AGENT": ""}), \
                mock.patch("checkin.create_retry_session"):
            with checkin.AccountClient(panel, "session=value") as client:
                self.assertEqual(client.user_agent, checkin.DEFAULT_USER_AGENT)

    def test_non_chromium_ua_has_no_chromium_client_hints(self):
        headers = checkin.generate_headers("session=value", checkin.PANELS[0], "Firefox/133.0")
        self.assertFalse(any(key.startswith("Sec-CH-") for key in headers))


class AccountClientTestCase(unittest.TestCase):
    setUp = RequestRetryTestCase.setUp
    response = RequestRetryTestCase.response

    def successful_responses(self):
        self.session.post.return_value = self.response()
        self.session.get.side_effect = [
            self.response(data={"code": 0, "data": {"leftDays": "3", "traffic": 0}}),
            self.response(data={"code": 0, "points": 19}),
        ]

    def test_one_session_for_entire_account(self):
        self.successful_responses()
        account = {"email": "private@example.com", "cookie": self.cookie}
        original = json.dumps(account)
        result = checkin.process_account(self.panel, account)
        self.assertTrue(result.success)
        self.assertEqual(result.points, "19")
        self.assertEqual(json.dumps(account), original)
        self.create.assert_called_once()
        self.session.close.assert_called_once()
        for call in self.session.post.call_args_list + self.session.get.call_args_list:
            self.assertTrue(call.kwargs["discard_cookies"])
            self.assertFalse(call.kwargs["default_headers"])

    def test_accounts_use_separate_sessions_and_cookie_jars(self):
        other = mock.Mock()
        self.create.side_effect = [self.session, other]
        with checkin.AccountClient(self.panel, self.cookie) as first, \
                checkin.AccountClient(self.panel, "gld:sess=other; gld:sess.sig=other-sig") as second:
            self.assertIsNot(first.session, second.session)
            self.assertIsNot(first.jar, second.jar)
            first.merge_cookies(self.response(headers={"Set-Cookie": "theme=light; Path=/"}), "/api/user/status")
            self.assertNotIn("theme=", second.headers("/api/user/status")["Cookie"])
        self.session.close.assert_called_once()
        other.close.assert_called_once()

    def test_rotated_cookie_is_used_by_followup_requests_without_mutating_config(self):
        self.successful_responses()
        self.session.post.return_value.headers = {
            "Set-Cookie": "gld:sess=rotated; Domain=.glados.cloud; Path=/; Secure, "
                          "gld:sess.sig=rotated-sig; Domain=.glados.cloud; Path=/; Secure",
        }
        account = {"email": "private@example.com", "cookie": self.cookie}
        result = checkin.process_account(self.panel, account)
        self.assertTrue(result.success)
        self.assertTrue(result.cookie_updated)
        self.assertEqual(account["cookie"], self.cookie)
        for call in self.session.get.call_args_list:
            value = call.kwargs["headers"]["Cookie"]
            self.assertIn("gld:sess=rotated", value)
            self.assertIn("gld:sess.sig=rotated-sig", value)
            self.assertNotIn("private-session", value)
            self.assertEqual(value.count("gld:sess="), 1)

    def test_deletion_or_expiry_of_session_stops_followups(self):
        for suffix in ("Max-Age=0", "Expires=Thu, 01 Jan 1970 00:00:00 GMT"):
            for name in ("gld:sess", "gld:sess.sig"):
                with self.subTest(suffix=suffix, name=name):
                    self.session.reset_mock()
                    self.session.post.return_value = self.response(headers={
                        "Set-Cookie": f"{name}=deleted; Domain=.glados.cloud; Path=/; {suffix}",
                    })
                    result = checkin.process_account(self.panel, {"cookie": self.cookie})
                    self.assertFalse(result.success)
                    self.assertIn("清除了", result.message)
                    self.session.get.assert_not_called()
                    self.session.close.assert_called_once()

    def test_duplicate_headers_and_expires_commas_are_supported(self):
        headers = mock.Mock()
        headers.get_list.return_value = [
            "gld:sess=new; Path=/; Expires=Wed, 01 Jan 2031 00:00:00 GMT",
            "gld:sess.sig=new-sig; Path=/",
        ]
        with checkin.AccountClient(self.panel, self.cookie) as client:
            client.merge_cookies(self.response(headers=headers), "/api/user/status")
            value = client.headers("/api/user/points")["Cookie"]
            self.assertIn("gld:sess=new", value)
            self.assertIn("gld:sess.sig=new-sig", value)
            self.assertNotIn("Expires", value)
        headers.get_list.assert_called_once_with("Set-Cookie")

    def test_cookie_domains_and_paths_are_filtered(self):
        with checkin.AccountClient(self.panel, self.cookie) as client:
            client.merge_cookies(self.response(headers={"Set-Cookie":
                "foreign=secret; Domain=example.com; Path=/, local=value; Path=/console",
            }), "/api/user/status")
            self.assertEqual(client.headers("/api/user/points")["Cookie"], self.cookie)
            self.assertIn("local=value", client.headers("/console/checkin")["Cookie"])
            self.assertNotIn("foreign=", client.headers("/console/checkin")["Cookie"])

    def test_denied_response_does_not_replace_valid_cookie(self):
        self.session.get.return_value = self.response(data={"code": -2}, headers={
            "Set-Cookie": "gld:sess=rejected; Path=/",
        })
        with checkin.AccountClient(self.panel, self.cookie) as client:
            with self.assertRaises(checkin.PanelRequestError):
                checkin.request_json(self.panel, self.cookie, "/api/user/status", client=client)
            self.assertEqual(client.headers("/api/user/status")["Cookie"], self.cookie)
            self.assertFalse(client.cookie_updated)

    def test_auth_or_device_failure_stops_remaining_account_requests(self):
        for data in ({"code": -2}, {"code": 0, "reason": "device-mismatch"}):
            with self.subTest(data=data):
                self.session.reset_mock()
                self.session.post.return_value = self.response(data=data)
                result = checkin.process_account(self.panel, {"cookie": self.cookie})
                self.assertFalse(result.success)
                self.session.post.assert_called_once()
                self.session.get.assert_not_called()
                self.session.close.assert_called_once()
        self.sleep.assert_not_called()

    def test_status_failure_stops_points_and_preserves_sign_result(self):
        self.session.post.return_value = self.response()
        self.session.get.return_value = self.response(data={"code": -2})
        result = checkin.process_account(self.panel, {"cookie": self.cookie})
        self.assertFalse(result.success)
        self.assertIn("签到成功", result.message)
        self.assertEqual(result.points, "未获取")
        self.session.get.assert_called_once()

    def test_unknown_and_contradictory_sign_results_fail(self):
        for data in ({"code": 9, "message": "Checkin! Got 3 Points"},
                     {"code": 0, "message": "unknown " + self.cookie},
                     {"code": 0}, {"code": 0, "message": "Please Try Tomorrow"},
                     {"code": 0, "message": "Error: Checkin! Got 3 Points"}):
            with self.subTest(data=data):
                self.session.reset_mock()
                self.session.post.return_value = self.response(data=data)
                result = checkin.process_account(self.panel, {"cookie": self.cookie})
                self.assertFalse(result.success)
                self.assertNotIn("private-session", result.message)
                self.session.get.assert_not_called()
        self.sleep.assert_not_called()

    def test_railgun_free_account_reward_continues_status_and_points_without_retry(self):
        panel = next(panel for panel in checkin.PANELS if panel["name"] == "Railgun")
        for code in (0, "0"):
            with self.subTest(code=code):
                self.session.reset_mock()
                self.session.post.return_value = self.response(data={
                    "code": code, "message": "Checkin! Got 2 Points (Free account - upgrade for more!)",
                })
                self.session.get.side_effect = [
                    self.response(data={"code": 0, "data": {"leftDays": 0, "traffic": 0}}),
                    self.response(data={"code": 0, "points": 19}),
                ]
                result = checkin.process_account(panel, {"cookie": self.cookie})
                self.assertTrue(result.success)
                self.assertEqual(result.message, "签到成功，获得2积分（免费账号奖励）")
                self.assertEqual(result.left_days, "0")
                self.assertEqual(result.points, "19")
                self.session.post.assert_called_once()
                self.assertEqual(self.session.get.call_count, 2)
                self.session.close.assert_called_once()
        self.sleep.assert_not_called()

    def test_railgun_free_account_reward_does_not_hide_errors_or_unknown_suffixes(self):
        panel = next(panel for panel in checkin.PANELS if panel["name"] == "Railgun")
        message = "Checkin! Got 2 Points (Free account - upgrade for more!)"
        cases = [{"code": code, "message": message} for code in (None, False, True, 1, "1", 9, -2)] + [
            {"code": 0, "message": "Error: " + message},
            {"code": 0, "message": message + " " + self.cookie},
            {"code": 0, "message": "Checkin! Got 2 Points (Unknown account notice)"},
            {"code": 0, "message": message, "reason": "device-mismatch"},
            {"code": 0, "message": [message]},
        ]
        for data in cases:
            with self.subTest(data=data):
                self.session.reset_mock()
                self.session.post.return_value = self.response(data=data)
                result = checkin.process_account(panel, {"cookie": self.cookie})
                self.assertFalse(result.success)
                self.assertNotIn("private-session", result.message)
                self.session.post.assert_called_once()
                self.session.get.assert_not_called()
        self.sleep.assert_not_called()

    def test_railgun_free_account_suffix_is_not_accepted_for_glados(self):
        self.session.post.return_value = self.response(data={
            "code": 0, "message": "Checkin! Got 2 Points (Free account - upgrade for more!)",
        })
        result = checkin.process_account(self.panel, {"cookie": self.cookie})
        self.assertFalse(result.success)
        self.session.get.assert_not_called()
        self.sleep.assert_not_called()

    def test_railgun_already_checked_in_continues_status_and_points_without_retry(self):
        panel = next(panel for panel in checkin.PANELS if panel["name"] == "Railgun")
        for code in (None, 0, "0", 1, "1"):
            with self.subTest(code=code):
                self.session.reset_mock()
                self.session.post.return_value = self.response(data={
                    "code": code, "message": "Already checked in today. Come back tomorrow.",
                })
                self.session.get.side_effect = [
                    self.response(data={"code": 0, "data": {"leftDays": 3, "traffic": 0}}),
                    self.response(data={"code": 0, "points": 19}),
                ]
                result = checkin.process_account(panel, {"cookie": self.cookie})
                self.assertTrue(result.success)
                self.assertEqual(result.message, "今日已签到")
                self.assertEqual(result.left_days, "3")
                self.assertEqual(result.points, "19")
                self.session.post.assert_called_once()
                self.assertEqual(self.session.get.call_count, 2)
                self.session.close.assert_called_once()
        self.sleep.assert_not_called()

    def test_railgun_already_checked_in_does_not_hide_errors(self):
        panel = next(panel for panel in checkin.PANELS if panel["name"] == "Railgun")
        message = "Already checked in today. Come back tomorrow."
        for data in ({"code": 9, "message": message}, {"code": -2, "message": message},
                     {"code": 1, "message": "Error: " + message}, {"code": 1, "message": [message]}):
            with self.subTest(data=data):
                self.session.reset_mock()
                self.session.post.return_value = self.response(data=data)
                result = checkin.process_account(panel, {"cookie": self.cookie})
                self.assertFalse(result.success)
                self.session.get.assert_not_called()
        self.sleep.assert_not_called()

    def test_valid_top_level_points_do_not_depend_on_nested_shape(self):
        self.session.get.return_value = self.response(data={"code": 0, "points": 8, "data": "unused"})
        self.assertEqual(checkin.fetch_points(self.panel, self.cookie), "8")

    def test_malformed_points_fail_instead_of_reporting_zero(self):
        for points in (None, True, -1, "nan", "inf", {}, []):
            with self.subTest(points=points):
                self.session.get.return_value = self.response(data={"points": points})
                self.assertEqual(checkin.fetch_points(self.panel, self.cookie), "获取失败")

    def test_malformed_status_data_fails(self):
        for data in ({}, {"leftDays": "nan"}, {"leftDays": True},
                     {"leftDays": 2, "traffic": -1}, {"leftDays": 2, "traffic": "nan"}):
            with self.subTest(data=data):
                self.session.get.return_value = self.response(data={"code": 0, "data": data})
                self.assertEqual(checkin.check_account_status(self.panel, self.cookie), ("获取失败", "获取失败"))

    def test_bad_account_config_never_creates_a_session(self):
        for account in ([], "bad", {}, {"cookie": []}, {"cookie": "not-a-header"},
                        {"cookie": "gld:sess=missing-signature"}, {"cookie": "gld:sess.sig=missing-session"},
                        {"cookie": "a=1; a=2"}, {"cookie": "a=1\r\nInjected: value"},
                        {"cookie": "a=1", "user_agent": 1}, {"cookie": "a=1", "impersonate": []}):
            with self.subTest(account=account):
                self.assertFalse(checkin.process_account(self.panel, account).success)
        self.create.assert_not_called()

    def test_cookie_prefix_and_opaque_values_are_preserved(self):
        value = "gld:sess=a.b+/==; gld:sess.sig=signature%2B"
        with checkin.AccountClient(self.panel, "Cookie: " + value) as client:
            self.assertEqual(client.headers("/api/user/status")["Cookie"], value)

    def test_diagnostic_is_status_only_without_sign_or_points(self):
        self.session.get.return_value = self.response(data={"code": 0, "data": {"leftDays": 3}})
        result = checkin.process_account(self.panel, {"cookie": self.cookie}, diagnostic=True)
        self.assertTrue(result.success)
        self.session.post.assert_not_called()
        self.session.get.assert_called_once()
        self.assertEqual(self.session.get.call_args.args[0], self.panel["origin"] + "/api/user/status")

    def test_logs_hide_account_labels_cookie_and_server_text(self):
        account = {"email": "private@example.com", "cookie": self.cookie}
        self.session.post.return_value = self.response(data={"code": 0, "message": "private-server " + self.cookie})
        with mock.patch.dict("os.environ", {"GLADOS_ACCOUNTS_JSON": json.dumps([account])}):
            result = checkin.checkin_panel(self.panel)
        self.assertIn("private@example.com", result.content)
        for private in ("private@example.com", "private-session", "private-signature", "private-server"):
            self.assertNotIn(private, str(self.print.call_args_list))
        self.assertNotIn("private-session", result.content)


class CheckinPanelTestCase(unittest.TestCase):
    def setUp(self):
        keys = [panel["accounts_env"] for panel in checkin.PANELS]
        env_patcher = mock.patch.dict("os.environ", {key: "" for key in keys}, clear=False)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

        print_patcher = mock.patch("builtins.print")
        print_patcher.start()
        self.addCleanup(print_patcher.stop)
        sleep_patcher = mock.patch("checkin.time.sleep")
        sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

    def test_面板未配置时返回none(self):
        self.assertIsNone(checkin.checkin_panel(checkin.PANELS[0]))

    def test_账号配置解析失败时返回提示(self):
        with mock.patch.dict("os.environ", {"GLADOS_ACCOUNTS_JSON": "not-json"}):
            title, content = checkin.checkin_panel(checkin.PANELS[0])
        self.assertIn("GLaDOS", title)
        self.assertIn("账号配置解析失败", content)

    def test_账号列表为空时跳过(self):
        with mock.patch.dict("os.environ", {"GLADOS_ACCOUNTS_JSON": "[]"}):
            self.assertIsNone(checkin.checkin_panel(checkin.PANELS[0]))

    def test_无cookie账号给出提示(self):
        raw = json.dumps([{"email": "a@b.c"}])
        with mock.patch.dict("os.environ", {"GLADOS_ACCOUNTS_JSON": raw}):
            title, content = checkin.checkin_panel(checkin.PANELS[0])
        self.assertIn("a@b.c", content)
        self.assertIn("无Cookie", content)


class RunTestCase(unittest.TestCase):
    def setUp(self):
        keys = [panel["accounts_env"] for panel in checkin.PANELS]
        env_patcher = mock.patch.dict("os.environ", {**{key: "" for key in keys}, "PANEL_DIAGNOSTIC_ONLY": ""}, clear=False)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        print_patcher = mock.patch("builtins.print")
        print_patcher.start()
        self.addCleanup(print_patcher.stop)

    def test_仅推送已配置的面板(self):
        raw = json.dumps([{"email": "a@b.c", "cookie": "session=value"}])
        with mock.patch.dict("os.environ", {"GLADOS_ACCOUNTS_JSON": raw}):
            with mock.patch("checkin.sign", return_value="签到成功，获得1积分 🎉"), \
                 mock.patch("checkin.check_account_status", return_value=("10", "1.000")), \
                 mock.patch("checkin.fetch_points", return_value="5"), \
                 mock.patch("checkin.create_retry_session"), \
                 mock.patch("checkin.time.sleep"), \
                 mock.patch("checkin.notify.send") as send:
                checkin.run()

        send.assert_called_once()
        title, content = send.call_args.args
        self.assertEqual(send.call_args.kwargs["scope"], "GLADOS")
        self.assertIn("GLaDOS", title)
        self.assertIn("a@b.c", content)
        self.assertIn("签到成功", content)
        self.assertIn("剩余", content)

    def test_全部未配置时不推送(self):
        with mock.patch("checkin.notify.send") as send:
            self.assertEqual(checkin.run(), 0)
        send.assert_not_called()

    def test_any_panel_failure_returns_nonzero_and_other_panel_still_runs(self):
        failed = checkin.PanelResult("failed", "safe failure", False)
        success = checkin.PanelResult("success", "safe success", True)
        for first in (failed, RuntimeError("private-cookie")):
            with self.subTest(first=type(first).__name__), \
                    mock.patch("checkin.checkin_panel", side_effect=[first, success]) as panel, \
                    mock.patch("checkin.notify.send", return_value={"channel": "sent"}):
                self.assertEqual(checkin.run(), 1)
                self.assertEqual(panel.call_count, 2)

    def test_notification_failure_returns_nonzero(self):
        success = checkin.PanelResult("title", "content", True)
        for result in ({"channel": "失败: RuntimeError"}, RuntimeError("private-key")):
            with self.subTest(result=type(result).__name__), \
                    mock.patch("checkin.checkin_panel", side_effect=[success, None]), \
                    mock.patch("checkin.notify.send", side_effect=[result]):
                self.assertEqual(checkin.run(), 1)

    def test_success_and_unconfigured_notifications_return_zero(self):
        success = checkin.PanelResult("title", "content", True)
        with mock.patch("checkin.checkin_panel", side_effect=[success, None]), \
                mock.patch("checkin.notify.send", return_value={"one": "sent", "two": "skipped"}):
            self.assertEqual(checkin.run(), 0)

    def test_diagnostic_environment_does_not_send_notifications(self):
        result = checkin.PanelResult("title", "content", True)
        with mock.patch.dict("os.environ", {"PANEL_DIAGNOSTIC_ONLY": "true"}), \
                mock.patch("checkin.checkin_panel", side_effect=[result, None]) as panel, \
                mock.patch("checkin.notify.send") as send:
            self.assertEqual(checkin.run(), 0)
        send.assert_not_called()
        self.assertTrue(all(call.kwargs["diagnostic"] for call in panel.call_args_list))

    def test_invalid_diagnostic_flag_fails_without_executing(self):
        with mock.patch.dict("os.environ", {"PANEL_DIAGNOSTIC_ONLY": "typo"}), \
                mock.patch("checkin.checkin_panel") as panel, \
                mock.patch("checkin.notify.send") as send:
            self.assertEqual(checkin.run(), 1)
        panel.assert_not_called()
        send.assert_not_called()

    def test_cli_diagnose_and_exit_code_wiring(self):
        with mock.patch("checkin.run", return_value=1) as run:
            self.assertEqual(checkin.main(["--diagnose"]), 1)
        run.assert_called_once_with(diagnostic=True)
        with mock.patch("checkin.run", return_value=0) as run:
            self.assertEqual(checkin.main([]), 0)
        run.assert_called_once_with(diagnostic=None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
