# -- coding: utf-8 --
"""通知模块单元测试。

用桩替换 requests.request，覆盖以下场景：
- 未配置渠道时全部跳过
- 各渠道正常推送（含钉钉加签、Server酱两种 Key、Bark 地址解析、旧变量兼容）
- 站点级覆盖（{SCOPE}_{变量名} 优先于全局变量，未配置部分回退全局）
- 接口返回业务错误时标记失败且不影响其他渠道
- 超长内容按字符数与字节数截断
"""
import base64
import hashlib
import hmac
import os
import sys
import unittest
from unittest import mock
from urllib.parse import quote_plus

# 通知模块位于仓库根目录，补入搜索路径后以任意工作目录运行测试都能导入
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import notify

# 各渠道所有相关环境变量，测试前统一清空避免真实配置干扰
ENV_KEYS = (
    "TG_BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "TG_USER_ID", "TG_CHAT_ID", "TELEGRAM_CHAT_ID",
    "TG_API_HOST", "TG_PROXY",
    "FEISHU_WEBHOOK", "FEISHU_KEY",
    "DINGTALK_WEBHOOK", "DINGTALK_ACCESS_TOKEN", "DINGTALK_SECRET",
    "WECOM_WEBHOOK", "WECHAT_WEBHOOK", "QYWX_WEBHOOK", "PUSH_WEBHOOK_URL",
    "WECOM_CORPID", "QYWX_CORPID", "WECOM_CORPSECRET", "QYWX_CORPSECRET",
    "WECOM_AGENTID", "QYWX_AGENTID", "WECOM_TOUSER", "QYWX_TOUSER",
    "BARK_URL",
    "SERVERCHAN_KEY", "SERVERCHAN_SENDKEY",
    "PUSHPLUS_TOKEN", "PUSHPLUS_KEY", "PUSHPLUS_TOPIC",
    "NTFY_URL", "NTFY_TOPIC", "NTFY_PRIORITY",
    "GOTIFY_URL", "GOTIFY_TOKEN", "GOTIFY_PRIORITY",
)

# 站点级覆盖变量（{SCOPE}_{变量名}），与全局变量一并清空避免真实配置干扰
_SCOPES = ("GLADOS", "RAILGUN", "NODESEEK", "LINUXSB")
ALL_ENV_KEYS = ENV_KEYS + tuple(f"{scope}_{key}" for scope in _SCOPES for key in ENV_KEYS)


class FakeResponse:
    """模拟 requests.Response，仅实现被 notify 使用的接口。"""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class NotifyTestCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict("os.environ", {key: "" for key in ALL_ENV_KEYS}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        # 重试等待会拖慢测试，直接跳过 sleep
        sleep_patcher = mock.patch("notify.time.sleep")
        sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

    def test_未配置渠道时全部跳过(self):
        with mock.patch("notify.requests.request") as request:
            results = notify.send("标题", "正文")
        request.assert_not_called()
        self.assertEqual(results, {name: "skipped" for name, _ in notify.CHANNELS})

    def test_telegram_推送成功并携带正确参数(self):
        with mock.patch.dict("os.environ", {"TG_BOT_TOKEN": "token123", "TG_USER_ID": "456"}):
            with mock.patch(
                "notify.requests.request", return_value=FakeResponse({"ok": True})
            ) as request:
                results = notify.send("标题", "正文")

        self.assertEqual(results["Telegram"], "sent")
        method, url = request.call_args.args
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://api.telegram.org/bottoken123/sendMessage")
        payload = request.call_args.kwargs["json"]
        self.assertEqual(payload["chat_id"], "456")
        self.assertEqual(payload["text"], "标题\n\n正文")

    def test_飞书_完整地址推送成功并携带正确参数(self):
        webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/abc123"
        with mock.patch.dict("os.environ", {"FEISHU_WEBHOOK": webhook}):
            with mock.patch(
                "notify.requests.request", return_value=FakeResponse({"code": 0})
            ) as request:
                results = notify.send("标题", "正文")

        self.assertEqual(results["飞书"], "sent")
        self.assertEqual(request.call_args.args[1], webhook)
        payload = request.call_args.kwargs["json"]
        self.assertEqual(payload["msg_type"], "text")
        self.assertEqual(payload["content"]["text"], "标题\n\n正文")

    def test_飞书_纯key自动拼接地址(self):
        with mock.patch.dict("os.environ", {"FEISHU_WEBHOOK": "abc-key"}):
            with mock.patch(
                "notify.requests.request", return_value=FakeResponse({"code": 0})
            ) as request:
                notify.send("标题", "正文")

        self.assertEqual(request.call_args.args[1], f"{notify.FEISHU_API}/abc-key")

    def test_钉钉_配置加签后按官方算法签名(self):
        fix_time = 1700000000.123
        env = {
            "DINGTALK_WEBHOOK": "https://oapi.dingtalk.com/robot/send?access_token=abc",
            "DINGTALK_SECRET": "sec",
        }
        with mock.patch.dict("os.environ", env):
            with mock.patch("notify.time.time", return_value=fix_time):
                with mock.patch(
                    "notify.requests.request", return_value=FakeResponse({"errcode": 0})
                ) as request:
                    results = notify.send("标题", "正文")

        self.assertEqual(results["钉钉"], "sent")
        timestamp = str(round(fix_time * 1000))
        expected_sign = quote_plus(
            base64.b64encode(
                hmac.new(b"sec", f"{timestamp}\nsec".encode("utf-8"), hashlib.sha256).digest()
            )
        )
        url = request.call_args.args[1]
        self.assertIn(f"timestamp={timestamp}", url)
        self.assertIn(f"sign={expected_sign}", url)
        self.assertEqual(request.call_args.kwargs["json"]["text"]["content"], "标题\n\n正文")

    def test_钉钉_纯token自动拼接地址(self):
        with mock.patch.dict("os.environ", {"DINGTALK_WEBHOOK": "tok123"}):
            with mock.patch(
                "notify.requests.request", return_value=FakeResponse({"errcode": 0})
            ) as request:
                notify.send("标题", "正文")

        self.assertEqual(request.call_args.args[1], f"{notify.DINGTALK_API}?access_token=tok123")

    def test_企业微信机器人推送成功(self):
        webhook = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc"
        with mock.patch.dict("os.environ", {"WECOM_WEBHOOK": webhook}):
            with mock.patch(
                "notify.requests.request", return_value=FakeResponse({"errcode": 0})
            ) as request:
                results = notify.send("标题", "正文")

        self.assertEqual(results["企业微信机器人"], "sent")
        self.assertEqual(request.call_args.args[1], webhook)
        self.assertEqual(request.call_args.kwargs["json"]["text"]["content"], "标题\n\n正文")

    def test_企业微信机器人_兼容旧变量PUSH_WEBHOOK_URL(self):
        webhook = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=legacy"
        with mock.patch.dict("os.environ", {"PUSH_WEBHOOK_URL": webhook}):
            with mock.patch(
                "notify.requests.request", return_value=FakeResponse({"errcode": 0})
            ) as request:
                results = notify.send("标题", "正文")

        self.assertEqual(results["企业微信机器人"], "sent")
        self.assertEqual(request.call_args.args[1], webhook)

    # ---- 站点级覆盖：{SCOPE}_{变量名} 优先于全局变量，未配置则回退 ----

    def test_站点级覆盖_telegram优先于全局(self):
        env = {
            "TG_BOT_TOKEN": "global_token", "TG_USER_ID": "111",
            "GLADOS_TG_BOT_TOKEN": "glados_token", "GLADOS_TG_USER_ID": "222",
        }
        with mock.patch.dict("os.environ", env):
            with mock.patch(
                "notify.requests.request", return_value=FakeResponse({"ok": True})
            ) as request:
                results = notify.send("标题", "正文", scope="GLADOS")

        self.assertEqual(results["Telegram"], "sent")
        self.assertEqual(
            request.call_args.args[1], "https://api.telegram.org/botglados_token/sendMessage"
        )
        self.assertEqual(request.call_args.kwargs["json"]["chat_id"], "222")

    def test_站点级未配置时回退全局(self):
        env = {"TG_BOT_TOKEN": "global_token", "TG_USER_ID": "111"}
        with mock.patch.dict("os.environ", env):
            with mock.patch(
                "notify.requests.request", return_value=FakeResponse({"ok": True})
            ) as request:
                results = notify.send("标题", "正文", scope="GLADOS")

        self.assertEqual(results["Telegram"], "sent")
        self.assertEqual(
            request.call_args.args[1], "https://api.telegram.org/botglobal_token/sendMessage"
        )

    def test_站点级覆盖_企业微信机器人各站不同地址(self):
        global_webhook = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=global"
        glados_webhook = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=glados"
        env = {"WECOM_WEBHOOK": global_webhook, "GLADOS_WECOM_WEBHOOK": glados_webhook}
        with mock.patch.dict("os.environ", env):
            with mock.patch(
                "notify.requests.request", return_value=FakeResponse({"errcode": 0})
            ) as request:
                notify.send("标题", "正文", scope="GLADOS")
                self.assertEqual(request.call_args.args[1], glados_webhook)

                notify.send("标题", "正文")
                self.assertEqual(request.call_args.args[1], global_webhook)

    def test_站点级覆盖_站点级别名优先于全局规范名(self):
        # 站点级使用兼容别名（PUSH_WEBHOOK_URL 加前缀）也应压过全局规范名 WECOM_WEBHOOK
        global_webhook = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=global"
        railgun_webhook = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=railgun"
        env = {"WECOM_WEBHOOK": global_webhook, "RAILGUN_PUSH_WEBHOOK_URL": railgun_webhook}
        with mock.patch.dict("os.environ", env):
            with mock.patch(
                "notify.requests.request", return_value=FakeResponse({"errcode": 0})
            ) as request:
                notify.send("标题", "正文", scope="RAILGUN")
                self.assertEqual(request.call_args.args[1], railgun_webhook)

                notify.send("标题", "正文", scope="GLADOS")
                self.assertEqual(request.call_args.args[1], global_webhook)

    def test_站点级仅覆盖部分渠道_其余回退全局(self):
        env = {
            "TG_BOT_TOKEN": "global_token", "TG_USER_ID": "111",
            "BARK_URL": "https://api.day.app/globalkey",
            "GLADOS_BARK_URL": "https://api.day.app/gladoskey",
        }
        responses = [FakeResponse({"ok": True}), FakeResponse({"code": 200})]
        with mock.patch.dict("os.environ", env):
            with mock.patch("notify.requests.request", side_effect=responses) as request:
                results = notify.send("标题", "正文", scope="GLADOS")

        self.assertEqual(results["Telegram"], "sent")
        self.assertEqual(results["Bark"], "sent")
        # 第 1 个请求是 Telegram：未配站点级，回退全局 token
        self.assertIn("global_token", request.call_args_list[0].args[1])
        # 第 2 个请求是 Bark：走 GLADOS_BARK_URL 站点级地址
        self.assertEqual(request.call_args_list[1].args[1], "https://api.day.app/push")
        self.assertEqual(request.call_args_list[1].kwargs["json"]["device_key"], "gladoskey")

    def test_站点级_未配置任何渠道时全部跳过(self):
        with mock.patch("notify.requests.request") as request:
            results = notify.send("标题", "正文", scope="GLADOS")
        request.assert_not_called()
        self.assertEqual(results, {name: "skipped" for name, _ in notify.CHANNELS})

    def test_企业微信应用先取token再发消息(self):
        env = {"WECOM_CORPID": "corp", "WECOM_CORPSECRET": "secret", "WECOM_AGENTID": "1000002"}
        responses = [FakeResponse({"access_token": "tk"}), FakeResponse({"errcode": 0})]
        with mock.patch.dict("os.environ", env):
            with mock.patch("notify.requests.request", side_effect=responses) as request:
                results = notify.send("标题", "正文")

        self.assertEqual(results["企业微信应用"], "sent")
        self.assertEqual(request.call_count, 2)
        send_call = request.call_args_list[1]
        self.assertEqual(send_call.kwargs["params"]["access_token"], "tk")
        self.assertEqual(send_call.kwargs["json"]["agentid"], 1000002)
        self.assertEqual(send_call.kwargs["json"]["touser"], "@all")

    def test_bark_完整地址解析服务器与设备key(self):
        with mock.patch.dict("os.environ", {"BARK_URL": "https://api.day.app/devicekey"}):
            with mock.patch(
                "notify.requests.request", return_value=FakeResponse({"code": 200})
            ) as request:
                results = notify.send("标题", "正文")

        self.assertEqual(results["Bark"], "sent")
        self.assertEqual(request.call_args.args[1], "https://api.day.app/push")
        payload = request.call_args.kwargs["json"]
        self.assertEqual(payload["device_key"], "devicekey")
        self.assertEqual(payload["title"], "标题")
        self.assertEqual(payload["body"], "正文")

    def test_bark_纯key使用默认服务器(self):
        with mock.patch.dict("os.environ", {"BARK_URL": "devicekey"}):
            with mock.patch(
                "notify.requests.request", return_value=FakeResponse({"code": 200})
            ) as request:
                notify.send("标题", "正文")

        self.assertEqual(request.call_args.args[1], f"{notify.BARK_DEFAULT_SERVER}/push")
        self.assertEqual(request.call_args.kwargs["json"]["device_key"], "devicekey")

    def test_serverchan_turbo(self):
        with mock.patch.dict("os.environ", {"SERVERCHAN_KEY": "SCT123"}):
            with mock.patch(
                "notify.requests.request", return_value=FakeResponse({"code": 0})
            ) as request:
                results = notify.send("标题", "正文")

        self.assertEqual(results["Server酱"], "sent")
        self.assertEqual(request.call_args.args[1], "https://sctapi.ftqq.com/SCT123.send")
        self.assertEqual(request.call_args.kwargs["data"]["title"], "标题")

    def test_serverchan_3按Key前缀路由到ft07(self):
        key = "sctp1234tabcdef"
        with mock.patch.dict("os.environ", {"SERVERCHAN_KEY": key}):
            with mock.patch(
                "notify.requests.request", return_value=FakeResponse({"code": 0})
            ) as request:
                notify.send("标题", "正文")

        self.assertEqual(request.call_args.args[1], f"https://1234.push.ft07.com/send/{key}.send")

    def test_pushplus_推送成功并携带topic(self):
        env = {"PUSHPLUS_TOKEN": "tk", "PUSHPLUS_TOPIC": "grp"}
        with mock.patch.dict("os.environ", env):
            with mock.patch(
                "notify.requests.request", return_value=FakeResponse({"code": 200})
            ) as request:
                results = notify.send("标题", "正文")

        self.assertEqual(results["PushPlus"], "sent")
        payload = request.call_args.kwargs["json"]
        self.assertEqual(payload["token"], "tk")
        self.assertEqual(payload["topic"], "grp")
        self.assertEqual(payload["template"], "txt")

    def test_ntfy_标题按RFC2047编码(self):
        env = {"NTFY_TOPIC": "mytopic", "NTFY_PRIORITY": "4"}
        with mock.patch.dict("os.environ", env):
            with mock.patch(
                "notify.requests.request", return_value=FakeResponse({"id": "x"})
            ) as request:
                results = notify.send("标题", "正文")

        self.assertEqual(results["ntfy"], "sent")
        self.assertEqual(request.call_args.args[1], "https://ntfy.sh/mytopic")
        headers = request.call_args.kwargs["headers"]
        expected_title = "=?utf-8?B?" + base64.b64encode("标题".encode("utf-8")).decode("utf-8") + "?="
        self.assertEqual(headers["Title"], expected_title)
        self.assertEqual(headers["Priority"], "4")

    def test_gotify_推送成功(self):
        env = {"GOTIFY_URL": "https://gotify.example.com", "GOTIFY_TOKEN": "tk"}
        with mock.patch.dict("os.environ", env):
            with mock.patch(
                "notify.requests.request", return_value=FakeResponse({"id": 1})
            ) as request:
                results = notify.send("标题", "正文")

        self.assertEqual(results["Gotify"], "sent")
        self.assertEqual(request.call_args.args[1], "https://gotify.example.com/message?token=tk")
        self.assertEqual(request.call_args.kwargs["json"]["message"], "正文")

    def test_单渠道失败不影响其他渠道(self):
        env = {
            "TG_BOT_TOKEN": "token",
            "TG_USER_ID": "1",
            "WECOM_WEBHOOK": "https://example.com/hook",
        }

        def fake_request(method, url, **kwargs):
            if "telegram" in url:
                return FakeResponse({"ok": False, "description": "chat not found"})
            return FakeResponse({"errcode": 0})

        with mock.patch.dict("os.environ", env):
            with mock.patch("notify.requests.request", side_effect=fake_request):
                results = notify.send("标题", "正文")

        self.assertIn("失败", results["Telegram"])
        self.assertEqual(results["企业微信机器人"], "sent")

    def test_网络异常重试到上限后标记失败(self):
        with mock.patch.dict("os.environ", {"TG_BOT_TOKEN": "t", "TG_USER_ID": "1"}):
            with mock.patch(
                "notify.requests.request", side_effect=RuntimeError("连接超时")
            ) as request:
                results = notify.send("标题", "正文")

        self.assertEqual(request.call_count, notify.MAX_RETRY)
        self.assertIn("连接超时", results["Telegram"])

    def test_超长内容按字符数截断(self):
        long_text = "长" * 5000
        with mock.patch.dict("os.environ", {"TG_BOT_TOKEN": "t", "TG_USER_ID": "1"}):
            with mock.patch(
                "notify.requests.request", return_value=FakeResponse({"ok": True})
            ) as request:
                notify.send("标题", long_text)

        text = request.call_args.kwargs["json"]["text"]
        self.assertEqual(len(text), notify.TELEGRAM_TEXT_LIMIT)
        self.assertTrue(text.endswith("..."))

    def test_超长内容按字节数截断(self):
        long_text = "长" * 5000
        with mock.patch.dict("os.environ", {"WECOM_WEBHOOK": "https://example.com/hook"}):
            with mock.patch(
                "notify.requests.request", return_value=FakeResponse({"errcode": 0})
            ) as request:
                notify.send("标题", long_text)

        content = request.call_args.kwargs["json"]["text"]["content"]
        self.assertLessEqual(len(content.encode("utf-8")), notify.WECOM_TEXT_BYTES_LIMIT)
        self.assertTrue(content.endswith("..."))


if __name__ == "__main__":
    unittest.main(verbosity=2)
