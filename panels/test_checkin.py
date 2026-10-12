# -- coding: utf-8 --
"""panels/checkin.py（GLaDOS 系面板签到）单元测试。

curl_cffi 仅在 CI 环境安装，本地测试用桩模块注入 sys.modules 后再导入被测模块；
统一通知模块的 send 替换为桩，验证内容组装与推送调用。
"""
import json
import sys
import unittest
from unittest import mock

# 注入 curl_cffi 桩模块：本机未安装该依赖时也能导入 checkin
if "curl_cffi" not in sys.modules:
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


class TranslateMessageTestCase(unittest.TestCase):
    def test_签到成功提取积分(self):
        self.assertIn("3", checkin.translate_message("Checkin! Got 3 Points"))

    def test_已知状态翻译(self):
        cases = {
            "Please Try Tomorrow": "明天再试",
            "Checkin Repeats! Please Try Tomorrow": "重复签到",
            "Today's observation logged. Return tomorrow for more points.": "已签到",
        }
        for raw, expected in cases.items():
            self.assertIn(expected, checkin.translate_message(raw))

    def test_未知状态(self):
        self.assertIn("未知", checkin.translate_message("Something new"))


class CheckinPanelTestCase(unittest.TestCase):
    def setUp(self):
        keys = [panel["accounts_env"] for panel in checkin.PANELS]
        env_patcher = mock.patch.dict("os.environ", {key: "" for key in keys}, clear=False)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
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
        env_patcher = mock.patch.dict("os.environ", {key: "" for key in keys}, clear=False)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

    def test_仅推送已配置的面板(self):
        raw = json.dumps([{"email": "a@b.c", "cookie": "ck"}])
        with mock.patch.dict("os.environ", {"GLADOS_ACCOUNTS_JSON": raw}):
            with mock.patch("checkin.sign", return_value="签到成功，获得1积分 🎉"), \
                 mock.patch("checkin.check_account_status", return_value=("10", "1.000")), \
                 mock.patch("checkin.fetch_points", return_value="5"), \
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
            checkin.run()
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
