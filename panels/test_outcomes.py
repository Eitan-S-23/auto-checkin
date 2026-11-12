"""Structured check-in outcomes, low-reward warnings and privacy."""
import json
import os
import unittest
from unittest import mock

import test_checkin

checkin = test_checkin.checkin


class OutcomeTestCase(unittest.TestCase):
    setUp = test_checkin.RequestRetryTestCase.setUp
    response = test_checkin.RequestRetryTestCase.response
    successful_responses = test_checkin.AccountClientTestCase.successful_responses

    def test_new_reward_preserves_safe_business_metadata(self):
        self.successful_responses()
        self.session.post.return_value = self.response(data={
            "code": "0", "message": "Checkin! Got 2 Points", "reason": "low-reward"})
        result = checkin.process_account(self.panel, {"cookie": self.cookie})
        self.assertTrue(result.success)
        self.assertEqual(result.checkin_outcome.category, "reward")
        self.assertEqual(result.checkin_outcome.reward, "2")
        self.assertEqual(result.checkin_outcome.code, "0")
        self.assertEqual(result.checkin_outcome.reason, "low-reward")
        self.assertEqual(result.checkin_outcome.origin, self.panel["origin"])
        self.assertTrue(checkin.low_reward_warning(self.panel, result.checkin_outcome))
        self.session.post.assert_called_once()
        self.sleep.assert_not_called()

    def test_recorded_observation_and_repeated_checkin_have_no_new_reward(self):
        for message, category in (("Today's observation logged. Return tomorrow for more points.", "observation"),
                                  ("Checkin Repeats! Please Try Tomorrow", "repeated"),
                                  ("Already checked in today. Come back tomorrow.", "repeated")):
            with self.subTest(category=category):
                self.session.reset_mock()
                self.successful_responses()
                self.session.post.return_value = self.response(data={"code": 1, "message": message})
                result = checkin.process_account(self.panel, {"cookie": self.cookie})
                self.assertTrue(result.success)
                self.assertEqual(result.checkin_outcome.category, category)
                self.assertIsNone(result.checkin_outcome.reward)
                self.assertEqual(checkin.low_reward_warning(self.panel, result.checkin_outcome), "")
                self.session.post.assert_called_once()

    def test_railgun_free_reward_is_not_a_low_reward_warning(self):
        self.successful_responses()
        self.session.post.return_value = self.response(data={
            "code": 0, "message": "Checkin! Got 2 Points (Free account - upgrade for more!)"})
        panel = next(panel for panel in checkin.PANELS if panel["name"] == "Railgun")
        result = checkin.process_account(panel, {"cookie": self.cookie})
        self.assertTrue(result.success)
        self.assertEqual(result.checkin_outcome.category, "free_reward")
        self.assertEqual(checkin.low_reward_warning(panel, result.checkin_outcome), "")

    def test_threshold_can_be_disabled_and_invalid_values_use_default(self):
        outcome = checkin.CheckinOutcome("reward", "2", "0", "missing", self.panel["origin"])
        with mock.patch.dict(os.environ, {"GLADOS_LOW_REWARD_THRESHOLD": "0"}):
            self.assertEqual(checkin.low_reward_warning(self.panel, outcome), "")
        for value in ("bad", "-1", "101", "nan"):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"GLADOS_LOW_REWARD_THRESHOLD": value}):
                self.assertTrue(checkin.low_reward_warning(self.panel, outcome))
        high = checkin.CheckinOutcome("reward", "11", "0", "missing", self.panel["origin"])
        self.assertEqual(checkin.low_reward_warning(self.panel, high), "")

    def test_unknown_reason_and_business_code_never_expose_raw_values(self):
        self.successful_responses()
        self.session.post.return_value = self.response(data={
            "code": 0, "message": "Checkin! Got 2 Points", "reason": "private-reason-session"})
        raw = json.dumps([{"cookie": self.cookie}])
        with mock.patch.dict(os.environ, {"GLADOS_ACCOUNTS_JSON": raw}):
            result = checkin.checkin_panel(self.panel)
        self.assertTrue(result.success)
        self.assertIn("reason=other", result.content)
        self.assertNotIn("private-reason-session", result.content + str(self.print.call_args_list))
        self.assertNotIn("private-session", result.content + str(self.print.call_args_list))
        self.session.post.return_value = self.response(data={
            "code": "private-code", "message": "private-message", "reason": "private-reason"})
        failed = checkin.process_account(self.panel, {"cookie": self.cookie})
        self.assertFalse(failed.success)
        for value in ("private-code", "private-message", "private-reason"):
            self.assertNotIn(value, failed.message)

    def test_boolean_success_codes_are_rejected_without_followup_or_retry(self):
        for code in (False, True):
            with self.subTest(code=code):
                self.session.reset_mock()
                self.session.post.return_value = self.response(data={"code": code, "message": "Checkin! Got 2 Points"})
                result = checkin.process_account(self.panel, {"cookie": self.cookie})
                self.assertFalse(result.success)
                self.assertIsNone(result.checkin_outcome)
                self.session.post.assert_called_once()
                self.session.get.assert_not_called()
        self.sleep.assert_not_called()

    def test_followup_failure_keeps_completed_reward_metadata(self):
        self.successful_responses()
        self.session.get.side_effect = None
        self.session.get.return_value = self.response(data={"code": -2})
        result = checkin.process_account(self.panel, {"cookie": self.cookie})
        self.assertFalse(result.success)
        self.assertEqual(result.checkin_outcome.reward, "3")
        self.session.post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
