"""Offline regressions for GLaDOS origin failover and one-time proofs."""
import hashlib
import unittest
from unittest import mock

import test_checkin

checkin = test_checkin.checkin


class DomainFailoverTestCase(unittest.TestCase):
    response = test_checkin.RequestRetryTestCase.response

    def setUp(self):
        test_checkin.RequestRetryTestCase.setUp(self)
        self.panel = checkin.PANELS[0]
        self.primary = "https://glados.cloud"
        self.backup = "https://glados.rocks"
        self.last = "https://glados.network"
        self.session.post.return_value = self.response()
        self.status = self.response(data={"code": 0, "data": {"leftDays": 3, "traffic": 0}})
        self.points = self.response(data={"code": 0, "points": 19})

    def challenge(self, nonce="fresh-nonce", headers=None):
        return self.response(data={"code": 0, "nonce": nonce, "ttl": 300000}, headers=headers)

    def urls(self, method):
        return [call.args[0] for call in getattr(self.session, method).call_args_list]

    def assert_post(self, call, origin, nonce):
        self.assertEqual(call.args[0], origin + "/api/user/checkin")
        self.assertEqual(call.kwargs["headers"]["Origin"], origin)
        self.assertEqual(call.kwargs["headers"]["Referer"], origin + "/console/checkin")
        self.assertEqual(call.kwargs["headers"]["User-Agent"], checkin.DEFAULT_USER_AGENT)
        self.assertFalse(call.kwargs["allow_redirects"])
        self.assertEqual(call.kwargs["json"], {
            "token": origin.removeprefix("https://"), "nonce": nonce,
            "proof": hashlib.sha256((nonce + ".glados-observe-v1").encode()).hexdigest(),
        })

    def test_unavailable_primary_uses_backup_for_checkin_and_followup_reads(self):
        self.session.get.side_effect = [self.response(503), self.challenge(), self.status, self.points]
        result = checkin.process_account(self.panel, {"cookie": self.cookie})
        self.assertTrue(result.success)
        self.assertEqual(result.points, "19")
        self.assertEqual(self.urls("get"), [
            self.primary + "/api/user/checkin/challenge", self.backup + "/api/user/checkin/challenge",
            self.backup + "/api/user/status", self.backup + "/api/user/points",
        ])
        self.assert_post(self.session.post.call_args, self.backup, "fresh-nonce")
        self.assertEqual(self.session.post.call_args.kwargs["headers"]["Cookie"], self.cookie)
        self.sleep.assert_called_once_with(2.7)
        self.create.assert_called_once()
        self.session.close.assert_called_once()
        self.assertEqual(self.panel["origin"], self.primary)
        self.assertEqual(self.panel["token"], "glados.cloud")

    def test_retired_endpoints_and_redirects_try_only_approved_domains(self):
        for status in (301, 302, 303, 307, 308, 404, 410):
            with self.subTest(status=status):
                self.session.reset_mock()
                self.sleep.reset_mock()
                self.session.get.side_effect = [
                    self.response(status, headers={"Location": "https://untrusted.example/checkin"}), self.challenge()]
                self.assertIn("3", checkin.sign(self.panel, self.cookie))
                self.assertEqual(self.urls("get"), [
                    self.primary + "/api/user/checkin/challenge", self.backup + "/api/user/checkin/challenge"])
                self.assert_post(self.session.post.call_args, self.backup, "fresh-nonce")
                self.assertTrue(all(not call.kwargs["allow_redirects"] for call in self.session.get.call_args_list))

    def test_connection_failure_and_html_get_response_use_backup(self):
        html = self.response()
        html.json.side_effect = ValueError("private response")
        dns_error = RuntimeError(self.cookie)
        dns_error.code = 6
        for failure in (TimeoutError(self.cookie), ConnectionError(self.cookie), dns_error, html, self.response(data=[])):
            with self.subTest(failure=type(failure).__name__):
                self.session.reset_mock()
                self.session.get.side_effect = [failure, self.challenge()]
                self.assertIn("3", checkin.sign(self.panel, self.cookie))
                self.assert_post(self.session.post.call_args, self.backup, "fresh-nonce")

    def test_one_round_still_tries_all_three_domains(self):
        self.session.get.side_effect = [self.response(503), self.response(404), self.challenge()]
        with mock.patch.dict("os.environ", {"PANEL_MAX_ATTEMPTS": "1"}):
            self.assertIn("3", checkin.sign(self.panel, self.cookie))
        self.assertEqual(self.urls("get"), [
            origin + "/api/user/checkin/challenge" for origin in (self.primary, self.backup, self.last)])
        self.assert_post(self.session.post.call_args, self.last, "fresh-nonce")
        self.sleep.assert_called_once_with(2.7)

    def test_all_domains_unavailable_has_a_bounded_round_budget(self):
        self.session.get.return_value = self.response(503)
        result = checkin.process_account(self.panel, {"cookie": self.cookie})
        self.assertFalse(result.success)
        self.assertIn("3", result.message)
        self.assertEqual(self.session.get.call_count, 9)
        for offset in (0, 3, 6):
            self.assertEqual(set(self.urls("get")[offset:offset + 3]), {
                origin + "/api/user/checkin/challenge" for origin in (self.primary, self.backup, self.last)})
        self.session.post.assert_not_called()
        self.assertEqual(self.sleep.call_args_list, [mock.call(10), mock.call(20)])
        self.assertNotIn("private-session", result.message + str(self.print.call_args_list))

    def test_all_retired_endpoints_stop_after_one_pass(self):
        self.session.get.return_value = self.response(404)
        self.assertFalse(checkin.process_account(self.panel, {"cookie": self.cookie}).success)
        self.assertEqual(self.session.get.call_count, 3)
        self.session.post.assert_not_called()
        self.sleep.assert_not_called()

    def test_failed_post_switches_only_after_obtaining_a_fresh_challenge(self):
        for failure in (TimeoutError(self.cookie), self.response(503), self.response(404)):
            with self.subTest(failure=type(failure).__name__):
                self.session.reset_mock()
                self.sleep.reset_mock()
                self.session.get.side_effect = [self.challenge("nonce-a"), self.challenge("nonce-b")]
                self.session.post.side_effect = [failure, self.response(data={
                    "code": 1, "message": "Today's observation logged. Return tomorrow for more points."})]
                self.assertIn("已签到", checkin.sign(self.panel, self.cookie))
                self.assert_post(self.session.post.call_args_list[0], self.primary, "nonce-a")
                self.assert_post(self.session.post.call_args_list[1], self.backup, "nonce-b")
                self.assertEqual([call[0] for call in self.session.mock_calls if call[0] in ("get", "post")],
                                 ["get", "post", "get", "post"])
                self.assertEqual(self.sleep.call_args_list, [mock.call(2.7), mock.call(2.7)])
                for nonce in ("nonce-a", "nonce-b"):
                    self.assertNotIn(nonce, str(self.print.call_args_list))

    def test_auth_device_and_invalid_challenge_stop_without_switching(self):
        failures = [self.response(401), self.response(403), self.response(data={"code": -2}),
                    self.response(data={"code": 0, "reason": "device-mismatch"}),
                    self.response(data={"code": 9}), self.response(data={"code": 0, "nonce": ""})]
        for failure in failures:
            with self.subTest(status=failure.status_code):
                self.session.reset_mock()
                self.session.get.return_value = failure
                self.assertFalse(checkin.process_account(self.panel, {"cookie": self.cookie}).success)
                self.session.get.assert_called_once()
                self.session.post.assert_not_called()
                self.sleep.assert_not_called()

    def test_auth_denial_on_backup_stops_before_third_domain(self):
        self.session.get.side_effect = [self.response(503), self.response(403)]
        self.assertFalse(checkin.process_account(self.panel, {"cookie": self.cookie}).success)
        self.assertEqual(self.session.get.call_count, 2)
        self.session.post.assert_not_called()

    def test_post_denials_and_unknown_responses_are_not_replayed(self):
        malformed = self.response()
        malformed.json.side_effect = ValueError("private body")
        for response in (self.response(401), self.response(403), malformed,
                         self.response(data={"code": 9}), self.response(data={"code": -2})):
            with self.subTest(status=response.status_code):
                self.session.reset_mock()
                self.session.get.return_value = self.challenge()
                self.session.post.return_value = response
                self.assertFalse(checkin.process_account(self.panel, {"cookie": self.cookie}).success)
                self.session.get.assert_called_once()
                self.session.post.assert_called_once()

    def test_rate_limit_and_retry_after_keep_current_origin(self):
        for status, headers in ((429, {}), (429, {"Retry-After": "25"}), (503, {"Retry-After": "25"})):
            with self.subTest(status=status, headers=headers):
                self.session.reset_mock()
                self.sleep.reset_mock()
                self.session.get.side_effect = [self.response(status, headers=headers), self.challenge()]
                self.assertIn("3", checkin.sign(self.panel, self.cookie))
                self.assertEqual(self.urls("get"), [self.primary + "/api/user/checkin/challenge"] * 2)
                self.assertEqual(self.sleep.call_args_list, [mock.call(25 if headers else 10), mock.call(2.7)])

    def test_long_retry_after_and_certificate_errors_never_switch(self):
        certificate_error = RuntimeError(self.cookie)
        certificate_error.code = 60
        for failure in (self.response(503, headers={"Retry-After": "300"}), certificate_error):
            with self.subTest(failure=type(failure).__name__):
                self.session.reset_mock()
                self.session.get.side_effect = [failure]
                self.assertFalse(checkin.process_account(self.panel, {"cookie": self.cookie}).success)
                self.session.get.assert_called_once()
                self.session.post.assert_not_called()
                self.sleep.assert_not_called()

    def test_cookie_updates_are_isolated_and_restored_per_origin(self):
        rotated = {"Set-Cookie": "gld:sess=cloud-session; Path=/; Secure, gld:sess.sig=cloud-sig; Path=/; Secure"}
        self.session.get.side_effect = [self.challenge("a", rotated), self.challenge("b"),
                                        self.challenge("c"), self.challenge("d")]
        self.session.post.side_effect = [self.response(503)] * 3 + [self.response()]
        self.assertIn("3", checkin.sign(self.panel, self.cookie))
        calls = self.session.post.call_args_list
        self.assertEqual([call.args[0] for call in calls], [
            origin + "/api/user/checkin" for origin in (self.primary, self.backup, self.last, self.last)])
        self.assertIn("cloud-session", calls[0].kwargs["headers"]["Cookie"])
        for call in calls[1:]:
            self.assertEqual(call.kwargs["headers"]["Cookie"], self.cookie)
        # Revisiting an origin must retain its own rotated cookies.
        with checkin.AccountClient(self.panel, self.cookie) as client:
            client.merge_cookies(self.response(headers=rotated), "/api/user/status")
            self.assertTrue(client.switch_origin({self.primary}))
            self.assertEqual(client.headers("/api/user/status")["Cookie"], self.cookie)
            self.assertTrue(client.switch_origin({self.backup, self.last}))
            self.assertIn("cloud-session", client.headers("/api/user/status")["Cookie"])

    def test_cookie_deletion_is_not_bypassed_by_switching(self):
        self.session.get.return_value = self.challenge(headers={"Set-Cookie":
            "gld:sess=deleted; Path=/; Max-Age=0, gld:sess.sig=deleted; Path=/; Max-Age=0"})
        self.assertFalse(checkin.process_account(self.panel, {"cookie": self.cookie}).success)
        self.session.get.assert_called_once()
        self.session.post.assert_not_called()

    def test_followup_query_failure_does_not_repeat_successful_checkin(self):
        self.session.get.side_effect = [self.challenge(), self.response(503), self.status, self.points]
        result = checkin.process_account(self.panel, {"cookie": self.cookie})
        self.assertTrue(result.success)
        self.session.post.assert_called_once()
        self.assertEqual(self.urls("get"), [self.primary + "/api/user/checkin/challenge",
            self.primary + "/api/user/status", self.backup + "/api/user/status", self.backup + "/api/user/points"])

    def test_diagnostic_can_switch_but_never_gets_challenge_or_posts(self):
        self.session.get.side_effect = [self.response(404), self.status]
        self.assertTrue(checkin.process_account(self.panel, {"cookie": self.cookie}, diagnostic=True).success)
        self.assertEqual(self.urls("get"), [self.primary + "/api/user/status", self.backup + "/api/user/status"])
        self.session.post.assert_not_called()
        self.sleep.assert_not_called()

    def test_next_account_starts_with_primary_and_its_own_cookie(self):
        self.session.get.side_effect = [self.response(503), self.challenge(), self.status, self.points,
                                        self.challenge(), self.status, self.points]
        self.assertTrue(checkin.process_account(self.panel, {"cookie": self.cookie}).success)
        second_cookie = "gld:sess=second-session; gld:sess.sig=second-signature"
        self.assertTrue(checkin.process_account(self.panel, {"cookie": second_cookie}).success)
        self.assertEqual(self.session.post.call_args.args[0], self.primary + "/api/user/checkin")
        self.assertEqual(self.session.post.call_args.kwargs["headers"]["Cookie"], second_cookie)
        self.assertEqual(self.create.call_count, 2)

    def test_railgun_and_static_posts_have_no_cross_origin_replay(self):
        self.session.post.return_value = self.response(503)
        with mock.patch.dict("os.environ", {"PANEL_MAX_ATTEMPTS": "1"}):
            self.assertIn("失败", checkin.sign(checkin.PANELS[1], self.cookie))
            self.session.post.assert_called_once()
            self.assertEqual(self.urls("post"), ["https://railgun.info/api/user/checkin"])
            self.session.reset_mock()
            with self.assertRaises(checkin.PanelRequestError):
                checkin.request_json(self.panel, self.cookie, "/api/user/checkin", "POST", {"token": "glados.cloud"})
            self.session.post.assert_called_once()
            self.assertEqual(self.urls("post"), [self.primary + "/api/user/checkin"])
        self.session.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
