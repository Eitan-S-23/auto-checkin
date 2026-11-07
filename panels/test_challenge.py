"""Regression tests for the one-time GLaDOS check-in challenge."""
import hashlib
import json
import unittest
from unittest import mock

import test_checkin

checkin = test_checkin.checkin


class CheckinChallengeTestCase(unittest.TestCase):
    response = test_checkin.RequestRetryTestCase.response

    def setUp(self):
        test_checkin.RequestRetryTestCase.setUp(self)
        self.panel = checkin.PANELS[0]
        patcher = mock.patch("checkin.time.monotonic", return_value=100.0)
        self.monotonic = patcher.start()
        self.addCleanup(patcher.stop)
        self.session.get.return_value = self.challenge()
        self.session.post.return_value = self.response()

    def challenge(self, nonce="private-nonce", ttl=300000, headers=None):
        return self.response(data={"code": 0, "nonce": nonce, "ttl": ttl}, headers=headers)

    def assert_proof(self, payload, nonce):
        expected = hashlib.sha256((nonce + ".glados-observe-v1").encode("utf-8")).hexdigest()
        self.assertEqual(payload, {"token": "glados.cloud", "nonce": nonce, "proof": expected})

    def test_success_uses_challenge_proof_and_wait_before_post(self):
        self.assertTrue(self.panel["checkin_challenge"])
        self.sleep.side_effect = lambda _: self.session.post.assert_not_called()
        result = checkin.sign(self.panel, self.cookie)
        self.assertEqual(result, checkin.translate_message("Checkin! Got 3 Points"))
        self.session.get.assert_called_once()
        self.assertEqual(self.session.get.call_args.args[0], "https://glados.cloud/api/user/checkin/challenge")
        self.session.post.assert_called_once()
        self.assert_proof(self.session.post.call_args.kwargs["json"], "private-nonce")
        self.sleep.assert_called_once_with(2.7)
        for call in (self.session.get.call_args, self.session.post.call_args):
            self.assertEqual(call.kwargs["headers"]["Cookie"], self.cookie)
            self.assertEqual(call.kwargs["headers"]["User-Agent"], checkin.DEFAULT_USER_AGENT)
            self.assertFalse(call.kwargs["allow_redirects"])
        self.create.assert_called_once()
        self.session.close.assert_called_once()

    def test_timeout_fetches_new_nonce_before_duplicate_response(self):
        self.session.get.side_effect = [self.challenge("nonce-a"), self.challenge("nonce-b")]
        message = "Today's observation logged. Return tomorrow for more points."
        self.session.post.side_effect = [TimeoutError(self.cookie), self.response(data={"code": 1, "message": message})]
        self.assertEqual(checkin.sign(self.panel, self.cookie), checkin.translate_message(message))
        self.assertEqual(self.session.get.call_count, 2)
        self.assertEqual(self.session.post.call_count, 2)
        for call, nonce in zip(self.session.post.call_args_list, ("nonce-a", "nonce-b")):
            self.assert_proof(call.kwargs["json"], nonce)
        self.assertEqual(self.sleep.call_args_list, [mock.call(2.7), mock.call(10), mock.call(2.7)])
        methods = [call[0] for call in self.session.mock_calls if call[0] in ("get", "post")]
        self.assertEqual(methods, ["get", "post", "get", "post"])

    def test_http_retry_after_also_refreshes_challenge(self):
        self.session.get.side_effect = [self.challenge("nonce-a"), self.challenge("nonce-b")]
        self.session.post.side_effect = [self.response(503, headers={"Retry-After": "25"}), self.response()]
        checkin.sign(self.panel, self.cookie)
        self.assert_proof(self.session.post.call_args.kwargs["json"], "nonce-b")
        self.assertEqual(self.sleep.call_args_list, [mock.call(2.7), mock.call(25), mock.call(2.7)])

    def test_challenge_get_failure_has_no_nested_retries_or_early_post(self):
        self.session.get.side_effect = [self.response(503), self.challenge()]
        checkin.sign(self.panel, self.cookie)
        self.assertEqual(self.session.get.call_count, 2)
        self.session.post.assert_called_once()
        self.assertEqual(self.sleep.call_args_list, [mock.call(10), mock.call(2.7)])

    def test_auth_device_and_redirect_failures_never_post(self):
        responses = [self.response(401), self.response(403), self.response(302),
                     self.response(data={"code": -2, "message": self.cookie}),
                     self.response(data={"code": 0, "reason": "device-mismatch"})]
        for response in responses:
            with self.subTest(response=response.status_code):
                self.session.reset_mock()
                self.sleep.reset_mock()
                self.session.get.return_value = response
                result = checkin.process_account(self.panel, {"cookie": self.cookie})
                self.assertFalse(result.success)
                self.session.get.assert_called_once()
                self.session.post.assert_not_called()
                self.sleep.assert_not_called()
                self.assertNotIn("private-session", result.message)

    def test_malformed_challenges_do_not_fall_back_to_token_only(self):
        cases = [{"code": value, "nonce": "nonce"} for value in (None, False, True, 0.0, 1, 9)]
        cases += [{"code": 0, "nonce": value} for value in (None, "", "   ", 42, [], "x" * 4097, "\ud800")]
        cases += [{"code": 0, "nonce": "nonce", "ttl": value}
                  for value in (None, False, True, "300000", 0, -1, 2700, float("nan"), float("inf"))]
        for data in cases:
            with self.subTest(data_type=type(data.get("nonce")).__name__, ttl_type=type(data.get("ttl")).__name__):
                self.session.reset_mock()
                self.sleep.reset_mock()
                self.session.get.return_value = self.response(data=data)
                with checkin.AccountClient(self.panel, self.cookie) as client:
                    with self.assertRaises(checkin.PanelRequestError) as caught:
                        checkin.sign(self.panel, self.cookie, client=client)
                self.assertEqual(caught.exception.kind, "challenge")
                self.session.get.assert_called_once()
                self.session.post.assert_not_called()
                self.sleep.assert_not_called()

    def test_missing_ttl_uses_default_and_string_zero_code_is_supported(self):
        self.session.get.return_value = self.response(data={"code": "0", "nonce": "nonce"})
        self.assertEqual(checkin.sign(self.panel, self.cookie), checkin.translate_message("Checkin! Got 3 Points"))
        self.assert_proof(self.session.post.call_args.kwargs["json"], "nonce")

    def test_expired_challenge_before_wait_is_not_submitted(self):
        self.session.get.return_value = self.challenge(ttl=3000)
        self.monotonic.side_effect = [0, 4]
        with mock.patch.dict("os.environ", {"PANEL_MAX_ATTEMPTS": "1"}), checkin.AccountClient(self.panel, self.cookie) as client:
            with self.assertRaises(checkin.PanelRequestError) as caught:
                checkin.sign(self.panel, self.cookie, client=client)
        self.assertEqual(caught.exception.kind, "challenge")
        self.sleep.assert_not_called()
        self.session.post.assert_not_called()

    def test_expiry_during_wait_fetches_a_new_challenge(self):
        self.session.get.side_effect = [self.challenge("expired", ttl=3000), self.challenge("fresh")]
        self.monotonic.side_effect = [0, 0, 3.5, 10, 10, 12.7]
        self.assertEqual(checkin.sign(self.panel, self.cookie), checkin.translate_message("Checkin! Got 3 Points"))
        self.assertEqual(self.session.get.call_count, 2)
        self.session.post.assert_called_once()
        self.assert_proof(self.session.post.call_args.kwargs["json"], "fresh")
        self.assertEqual(self.sleep.call_args_list, [mock.call(2.7), mock.call(10), mock.call(2.7)])

    def test_retry_exhaustion_is_bounded_and_does_not_log_nonce_or_proof(self):
        nonces = ["private-nonce-a", "private-nonce-b", "private-nonce-c"]
        self.session.get.side_effect = [self.challenge(nonce) for nonce in nonces]
        self.session.post.return_value = self.response(503)
        result = checkin.sign(self.panel, self.cookie)
        self.assertEqual(self.session.get.call_count, 3)
        self.assertEqual(self.session.post.call_count, 3)
        for call, nonce in zip(self.session.post.call_args_list, nonces):
            self.assert_proof(call.kwargs["json"], nonce)
            self.assertNotIn(nonce, result + str(self.print.call_args_list))
            self.assertNotIn(call.kwargs["json"]["proof"], result + str(self.print.call_args_list))
        self.assertNotIn("private-session", result + str(self.print.call_args_list))
        self.assertEqual(self.sleep.call_args_list, [mock.call(2.7), mock.call(10), mock.call(2.7), mock.call(20), mock.call(2.7)])

    def test_complete_account_flow_continues_status_and_points(self):
        self.session.get.side_effect = [self.challenge(),
            self.response(data={"code": 0, "data": {"leftDays": 3, "traffic": 0}}),
            self.response(data={"code": 0, "points": 19})]
        result = checkin.process_account(self.panel, {"cookie": self.cookie})
        self.assertTrue(result.success)
        self.assertEqual(result.points, "19")
        self.assertEqual([call.args[0] for call in self.session.get.call_args_list], [
            "https://glados.cloud/api/user/checkin/challenge", "https://glados.cloud/api/user/status",
            "https://glados.cloud/api/user/points"])
        self.create.assert_called_once()
        self.session.close.assert_called_once()

    def test_cookie_rotation_during_challenge_is_used_by_post(self):
        self.session.get.return_value = self.challenge(headers={"Set-Cookie":
            "gld:sess=rotated; Domain=.glados.cloud; Path=/; Secure, "
            "gld:sess.sig=rotated-sig; Domain=.glados.cloud; Path=/; Secure"})
        checkin.sign(self.panel, self.cookie)
        header = self.session.post.call_args.kwargs["headers"]["Cookie"]
        self.assertIn("gld:sess=rotated", header)
        self.assertIn("gld:sess.sig=rotated-sig", header)
        self.assertNotIn("private-session", header)

    def test_diagnostic_is_still_status_only_with_challenge_enabled(self):
        self.session.get.return_value = self.response(data={"code": 0, "data": {"leftDays": 3}})
        result = checkin.process_account(self.panel, {"cookie": self.cookie}, diagnostic=True)
        self.assertTrue(result.success)
        self.session.get.assert_called_once()
        self.assertEqual(self.session.get.call_args.args[0], "https://glados.cloud/api/user/status")
        self.session.post.assert_not_called()
        self.sleep.assert_not_called()

    def test_accounts_do_not_share_nonce_or_session(self):
        second = mock.Mock()
        self.create.side_effect = [self.session, second]
        for session, nonce in ((self.session, "first-nonce"), (second, "second-nonce")):
            session.post.return_value = self.response()
            session.get.side_effect = [self.challenge(nonce),
                self.response(data={"code": 0, "data": {"leftDays": 3}}), self.response(data={"points": 19})]
        accounts = [{"cookie": self.cookie}, {"cookie": "gld:sess=second; gld:sess.sig=second-sig"}]
        with mock.patch.dict("os.environ", {"GLADOS_ACCOUNTS_JSON": json.dumps(accounts)}), \
                mock.patch("checkin.random.randint", return_value=2):
            result = checkin.checkin_panel(self.panel)
        self.assertTrue(result.success)
        self.assert_proof(self.session.post.call_args.kwargs["json"], "first-nonce")
        self.assert_proof(second.post.call_args.kwargs["json"], "second-nonce")
        self.assertNotEqual(self.session.post.call_args.kwargs["headers"]["Cookie"], second.post.call_args.kwargs["headers"]["Cookie"])
        self.assertEqual(self.create.call_count, 2)

    def test_unicode_nonce_is_hashed_as_utf8_without_rewriting(self):
        nonce = "nonce-\u6d4b\u8bd5"
        self.session.get.return_value = self.challenge(nonce)
        checkin.sign(self.panel, self.cookie)
        self.assert_proof(self.session.post.call_args.kwargs["json"], nonce)

    def test_railgun_keeps_legacy_protocol(self):
        panel = checkin.PANELS[1]
        self.assertFalse(panel.get("checkin_challenge", False))
        self.assertEqual(checkin.sign(panel, self.cookie), checkin.translate_message("Checkin! Got 3 Points"))
        self.session.get.assert_not_called()
        self.session.post.assert_called_once()
        self.assertEqual(self.session.post.call_args.kwargs["json"], {"token": "railgun.info"})
        self.sleep.assert_not_called()

    def test_unknown_post_business_response_is_not_retried(self):
        self.session.post.return_value = self.response(data={"code": 9, "message": "unknown private-nonce"})
        result = checkin.process_account(self.panel, {"cookie": self.cookie})
        self.assertFalse(result.success)
        self.session.get.assert_called_once()
        self.session.post.assert_called_once()
        self.assertNotIn("private-nonce", result.message)
        self.sleep.assert_called_once_with(2.7)


if __name__ == "__main__":
    unittest.main()
