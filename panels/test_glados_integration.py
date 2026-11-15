"""Browser adapter integration without network, mail or real account credentials."""
import json
import os
import unittest
from unittest import mock

import test_checkin
from glados_browser import BrowserReply
from glados_common import GladosError

checkin = test_checkin.checkin


class BrowserAdapterTestCase(unittest.TestCase):
    response = test_checkin.RequestRetryTestCase.response

    def setUp(self):
        test_checkin.RequestRetryTestCase.setUp(self)
        patcher = mock.patch.dict(os.environ, {"GLADOS_BROWSER_CHECKIN": "0", "GLADOS_VERIFY_ONLY": "0",
            "GLADOS_GMAIL_ADDRESS": "", "GLADOS_GMAIL_APP_PASSWORD": "", "GLADOS_COOKIE_STORE_KEY": ""})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.browser = mock.Mock(user_agent=checkin.DEFAULT_USER_AGENT.replace("124.0.0.0", "157.0.0.0"))
        self.browser.run.return_value = self.reply()
        self.session.get.side_effect = [self.response(data={"code": 0, "data": {"leftDays": 10, "traffic": 0}}),
                                        self.response(data={"code": 0, "points": 9})]
        self.account = {"email": "alice@example.net", "cookie": self.cookie}

    def reply(self, payload=None, origin="glados.cloud"):
        return BrowserReply(payload or {"code": 0, "message": "Checkin! Got 3 Points"}, [
            {"name": "gld:sess", "value": "browser-session", "domain": origin},
            {"name": "gld:sess.sig", "value": "browser-signature", "domain": origin}])

    def test_native_reply_uses_existing_outcomes_and_browser_cookies_for_queries(self):
        result = checkin.process_browser_account(self.panel, self.account, self.browser)
        self.assertTrue(result.success)
        self.assertEqual(result.checkin_outcome.reward, "3")
        self.session.post.assert_not_called()
        for call in self.session.get.call_args_list:
            self.assertEqual(call.kwargs["headers"]["User-Agent"], self.browser.user_agent)
            self.assertEqual(call.kwargs["headers"]["Accept-Language"], "en-US")
            self.assertIn("browser-session", call.kwargs["headers"]["Cookie"])
        self.create.assert_called_once_with(mock.ANY, trust_env=False)
        self.browser.run.assert_called_once()
        self.assertEqual(self.browser.run.call_args.kwargs["expected_email"], "alice@example.net")

    def test_browser_ua_does_not_inherit_legacy_http_override(self):
        with mock.patch.dict(os.environ, {"GLADOS_USER_AGENT": "invalid\r\nlegacy-UA"}):
            result = checkin.process_browser_account(self.panel, self.account, self.browser)
        self.assertTrue(result.success)
        for call in self.session.get.call_args_list:
            self.assertEqual(call.kwargs["headers"]["User-Agent"], self.browser.user_agent)

    def test_unknown_submitted_response_is_never_replayed_or_reauthenticated(self):
        self.browser.run.side_effect = GladosError("result unknown", submitted=True, fallback=True)
        recovery = mock.Mock()
        recovery.prepare.return_value = False
        result = checkin.process_browser_account(self.panel, self.account, self.browser, recovery=recovery)
        self.assertFalse(result.success)
        self.browser.run.assert_called_once()
        recovery.login.assert_not_called()
        self.session.get.assert_not_called()
        self.session.post.assert_not_called()

    def test_turnstile_denial_is_explicit_and_does_not_request_email(self):
        self.browser.run.return_value = self.reply({"code": 5, "reason": "turnstile", "message": "private-upstream-message"})
        recovery = mock.Mock()
        recovery.prepare.return_value = False
        result = checkin.process_browser_account(self.panel, self.account, self.browser, recovery=recovery)
        self.assertFalse(result.success)
        self.assertIn("reason=turnstile", result.message)
        self.assertNotIn("private-upstream-message", result.message)
        self.browser.run.assert_called_once()
        recovery.login.assert_not_called()

    def test_device_rejection_can_recover_once_but_not_loop(self):
        recovery = mock.Mock()
        recovery.prepare.return_value = False
        self.browser.run.side_effect = [self.reply({"code": 4, "reason": "device-mismatch"}), self.reply()]
        result = checkin.process_browser_account(self.panel, self.account, self.browser, recovery=recovery)
        self.assertTrue(result.success)
        self.assertEqual(self.browser.run.call_count, 2)
        recovery.login.assert_called_once()
        self.browser.run.reset_mock()
        self.browser.run.side_effect = None
        self.browser.run.return_value = self.reply({"code": 4, "reason": "device-mismatch"})
        recovery.login.reset_mock()
        result = checkin.process_browser_account(self.panel, self.account, self.browser, recovery=recovery)
        self.assertFalse(result.success)
        self.assertEqual(self.browser.run.call_count, 2)
        recovery.login.assert_called_once()

    def test_initial_email_login_is_not_repeated_after_device_rejection(self):
        recovery = mock.Mock()
        recovery.prepare.return_value = True
        self.browser.run.return_value = self.reply({"code": 4})
        self.assertFalse(checkin.process_browser_account(self.panel, self.account, self.browser, recovery=recovery).success)
        recovery.login.assert_not_called()

    def test_auth_rejection_with_deleted_signature_can_recover(self):
        recovery = mock.Mock()
        recovery.prepare.return_value = False
        self.browser.run.side_effect = [BrowserReply({"code": -2}, [
            {"name": "gld:sess", "value": "incomplete", "domain": "glados.cloud"}]), self.reply()]
        result = checkin.process_browser_account(self.panel, self.account, self.browser, recovery=recovery)
        self.assertTrue(result.success)
        self.assertEqual(self.browser.run.call_count, 2)
        recovery.login.assert_called_once()

    def test_fallback_is_only_before_submission_and_without_email_recovery(self):
        panel = dict(checkin.PANELS[0])
        self.browser.run.side_effect = [GladosError("connection failed", fallback=True), self.reply(origin="glados.rocks")]
        result = checkin.process_browser_account(panel, self.account, self.browser)
        self.assertTrue(result.success)
        origins = [call.args[0] for call in self.browser.run.call_args_list]
        self.assertEqual(origins, ["https://glados.cloud", "https://glados.rocks"])
        recovery = mock.Mock()
        recovery.prepare.return_value = False
        self.browser.run.reset_mock()
        self.browser.run.side_effect = GladosError("connection failed", fallback=True)
        result = checkin.process_browser_account(panel, self.account, self.browser, recovery=recovery)
        self.assertFalse(result.success)
        self.browser.run.assert_called_once()
        recovery.login.assert_not_called()

    def test_verify_only_does_not_write_cache_login_or_reward(self):
        self.browser.run.return_value = BrowserReply({"challenge": True, "ready": True, "turnstile_required": True})
        recovery, store = mock.Mock(), mock.Mock()
        store.get.return_value = {}
        result = checkin.process_browser_account(self.panel, self.account, self.browser, recovery=recovery,
                                                store=store, verify_only=True)
        self.assertTrue(result.success)
        self.assertIsNone(result.checkin_outcome)
        self.browser.run.assert_called_once_with(mock.ANY, mock.ANY, expected_email="alice@example.net", verify_only=True)
        recovery.prepare.assert_not_called()
        recovery.login.assert_not_called()
        store.put.assert_not_called()
        self.session.post.assert_not_called()

    def test_no_credentials_in_unexpected_browser_exception(self):
        self.browser.run.side_effect = RuntimeError("private-session private-proxy private-token")
        result = checkin.process_browser_account(self.panel, self.account, self.browser)
        self.assertFalse(result.success)
        for value in ("private-session", "private-proxy", "private-token"):
            self.assertNotIn(value, result.message + str(self.print.call_args_list))

    def test_legacy_http_turnstile_denial_has_safe_reason_and_no_retry(self):
        self.session.post.return_value = self.response(data={"code": 5, "reason": "turnstile", "message": "private-body"})
        result = checkin.process_account(self.panel, self.account)
        self.assertFalse(result.success)
        self.assertIn("reason=turnstile", result.message)
        self.session.post.assert_called_once()
        self.sleep.assert_not_called()
        self.assertNotIn("private-body", result.message)

    def test_browser_initialization_failure_does_not_stop_railgun(self):
        raw = json.dumps([self.account])
        with mock.patch.dict(os.environ, {"GLADOS_BROWSER_CHECKIN": "1", "GLADOS_ACCOUNTS_JSON": raw,
                                         "RAILGUN_ACCOUNTS_JSON": raw}), \
                mock.patch("checkin.NativeBrowser", side_effect=GladosError("startup failed")), \
                mock.patch("checkin.process_account", return_value=checkin.AccountResult(True, "ok")) as legacy, \
                mock.patch("checkin.notify.send", return_value=True):
            self.assertEqual(checkin.run(), 1)
        legacy.assert_called_once()
        self.assertEqual(legacy.call_args.args[0]["name"], "Railgun")

    def test_status_only_never_initializes_browser_or_reads_mail_settings(self):
        with mock.patch.dict(os.environ, {"GLADOS_BROWSER_CHECKIN": "1", "GLADOS_ACCOUNTS_JSON": json.dumps([self.account])}), \
                mock.patch("checkin.NativeBrowser") as native, mock.patch("checkin.MailSettings.from_environment") as settings:
            checkin.checkin_panel(self.panel, diagnostic=True)
        native.assert_not_called()
        settings.assert_not_called()

    def test_global_browser_verification_skips_other_sites_and_notifications(self):
        raw = json.dumps([self.account])
        result = checkin.AccountResult(True, "ready")
        with mock.patch.dict(os.environ, {"GLADOS_ACCOUNTS_JSON": raw, "RAILGUN_ACCOUNTS_JSON": raw}), \
                mock.patch("checkin.NativeBrowser"), mock.patch("checkin.process_browser_account", return_value=result) as process, \
                mock.patch("checkin.process_account") as legacy, mock.patch("checkin.notify.send") as notify:
            self.assertEqual(checkin.run(verify_only=True), 0)
        process.assert_called_once()
        legacy.assert_not_called()
        notify.assert_not_called()

    def test_conflicting_diagnostics_stop_before_any_account_side_effect(self):
        with mock.patch("checkin.checkin_panel") as panel, mock.patch("checkin.notify.send") as notify:
            self.assertEqual(checkin.run(diagnostic=True, verify_only=True), 1)
        panel.assert_not_called()
        notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
