# -- coding: utf-8 --
"""仓库统一通知推送模块。

按环境变量启用推送渠道，未配置的渠道自动跳过，任一渠道失败不影响其他渠道。
GLaDOS / Railgun / NodeSeek / DeepFlood / linux.sb 全部签到任务共用本模块：
在仓库 Secrets 里配置任意渠道即可生效，变量清单见各渠道函数与 README。

支持站点级覆盖：变量名前加站点前缀（GLADOS_ / RAILGUN_ / NODESEEK_ / LINUXSB_），
例如配置 GLADOS_WECOM_WEBHOOK 后 GLaDOS 的推送只走该地址；
站点级未配置时回退全局变量（如 WECOM_WEBHOOK），两者都没有则跳过该渠道。

支持渠道（渠道 API 协议参照 Sitoi/dailycheckin 等同型开源项目核对）：
- Telegram Bot（TG_BOT_TOKEN + TG_USER_ID，可选 TG_API_HOST / TG_PROXY）
- 飞书群机器人（FEISHU_WEBHOOK：完整地址或纯 key）
- 钉钉群机器人（DINGTALK_WEBHOOK：完整地址或 access_token，可选 DINGTALK_SECRET 加签）
- 企业微信群机器人（WECOM_WEBHOOK，兼容旧变量 PUSH_WEBHOOK_URL）
- 企业微信应用消息（WECOM_CORPID + WECOM_CORPSECRET + WECOM_AGENTID）
- Bark（BARK_URL：完整地址或设备 key）
- Server酱（SERVERCHAN_KEY：Turbo 与 Server酱³ 自动识别）
- PushPlus（PUSHPLUS_TOKEN，可选 PUSHPLUS_TOPIC）
- ntfy（NTFY_TOPIC，可选 NTFY_URL / NTFY_PRIORITY）
- Gotify（GOTIFY_URL + GOTIFY_TOKEN，可选 GOTIFY_PRIORITY）
"""
import base64
import hashlib
import hmac
import os
import re
import time
from urllib.parse import quote_plus

import requests

# 单次请求超时秒数与失败重试次数（网络抖动时重试，指数退避）
REQUEST_TIMEOUT = 20
MAX_RETRY = 3

# 各渠道单条消息长度上限，均预留余量：
# Telegram 4096 字符、企业微信 text 正文 2048 字节、ntfy 4096 字节
TELEGRAM_TEXT_LIMIT = 4000
WECOM_TEXT_BYTES_LIMIT = 1900
NTFY_TEXT_BYTES_LIMIT = 3800
# 其余渠道（飞书/钉钉/Bark/Server酱/PushPlus/Gotify）限制宽松，用通用字符数上限
TEXT_LIMIT = 4000

WECOM_API = "https://qyapi.weixin.qq.com/cgi-bin"
FEISHU_API = "https://open.feishu.cn/open-apis/bot/v2/hook"
DINGTALK_API = "https://oapi.dingtalk.com/robot/send"
BARK_DEFAULT_SERVER = "https://api.day.app"
SERVERCHAN_TURBO_API = "https://sctapi.ftqq.com"
PUSHPLUS_API = "https://www.pushplus.plus/send"
NTFY_DEFAULT_SERVER = "https://ntfy.sh"


def _env(*names, default="", scope=None):
    """按顺序取第一个非空环境变量，用于兼容多种命名习惯；顺带清理复制配置时残留的首尾引号。

    scope 非空时先逐个查找 "{SCOPE}_{NAME}"（站点级覆盖），全部未配置才回退全局名。
    站点级与全局跨层级比较，即站点配了任何别名都优先于全局的任何别名，
    例如 RAILGUN_PUSH_WEBHOOK_URL 优先于全局 WECOM_WEBHOOK。
    """
    scoped_keys = [f"{scope}_{name}" for name in names] if scope else []
    for key in scoped_keys + list(names):
        value = os.environ.get(key)
        if value and value.strip():
            return value.strip().strip("\"'").strip()
    return default


def _truncate_chars(text, limit):
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _truncate_bytes(text, limit):
    """按 UTF-8 字节数截断，避免企业微信/ntfy 因超长拒收。"""
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    return raw[: limit - 3].decode("utf-8", errors="ignore") + "..."


def _request(method, url, **kwargs):
    """带重试的 HTTP 请求，返回解析后的 JSON（非 JSON 响应抛异常）。"""
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    last_error = None
    for attempt in range(1, MAX_RETRY + 1):
        try:
            response = requests.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except Exception as error:
            last_error = error
            if attempt < MAX_RETRY:
                time.sleep(2 * attempt)
    raise last_error


def send_telegram(title, content, scope=None):
    """推送到 Telegram。TG_API_HOST 可指向自建反代，TG_PROXY 走 HTTP 代理。"""
    token = _env("TG_BOT_TOKEN", "TELEGRAM_BOT_TOKEN", scope=scope)
    chat_id = _env("TG_USER_ID", "TG_CHAT_ID", "TELEGRAM_CHAT_ID", scope=scope)
    if not token or not chat_id:
        return None

    api_host = _env("TG_API_HOST", default="https://api.telegram.org", scope=scope).rstrip("/")
    proxy = _env("TG_PROXY", scope=scope)
    proxies = {"http": proxy, "https": proxy} if proxy else None

    payload = {
        "chat_id": chat_id,
        "text": _truncate_chars(f"{title}\n\n{content}", TELEGRAM_TEXT_LIMIT),
        "disable_web_page_preview": True,
    }
    result = _request("POST", f"{api_host}/bot{token}/sendMessage", json=payload, proxies=proxies)
    if not result.get("ok"):
        raise RuntimeError(f"Telegram 返回失败: {result.get('description') or result}")
    return True


def send_feishu(title, content, scope=None):
    """推送到飞书群机器人。FEISHU_WEBHOOK 可填完整 Webhook 地址或纯 key。"""
    raw = _env("FEISHU_WEBHOOK", "FEISHU_KEY", scope=scope)
    if not raw:
        return None

    url = raw if "://" in raw else f"{FEISHU_API}/{raw}"
    payload = {
        "msg_type": "text",
        "content": {"text": _truncate_chars(f"{title}\n\n{content}", TEXT_LIMIT)},
    }
    result = _request("POST", url, json=payload)
    # 不同版本的飞书机器人返回 code 或 StatusCode，未识别到字段视为异常
    code = result.get("code", result.get("StatusCode", -1))
    if code != 0:
        raise RuntimeError(f"飞书返回失败: {result}")
    return True


def send_dingtalk(title, content, scope=None):
    """推送到钉钉群机器人。

    DINGTALK_WEBHOOK 可填完整地址或纯 access_token；
    安全设置选「加签」时把密钥填到 DINGTALK_SECRET，按官方算法签名。
    """
    raw = _env("DINGTALK_WEBHOOK", "DINGTALK_ACCESS_TOKEN", scope=scope)
    if not raw:
        return None

    url = raw if "://" in raw else f"{DINGTALK_API}?access_token={raw}"

    secret = _env("DINGTALK_SECRET", scope=scope)
    if secret:
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{secret}"
        hmac_code = hmac.new(
            secret.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
        ).digest()
        sign = quote_plus(base64.b64encode(hmac_code))
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}timestamp={timestamp}&sign={sign}"

    payload = {
        "msgtype": "text",
        "text": {"content": _truncate_chars(f"{title}\n\n{content}", TEXT_LIMIT)},
    }
    result = _request("POST", url, json=payload)
    if result.get("errcode") != 0:
        raise RuntimeError(f"钉钉返回失败: {result}")
    return True


def send_wecom_webhook(title, content, scope=None):
    """推送到企业微信群机器人。末尾兼容旧变量 PUSH_WEBHOOK_URL（合并前面板仓库的配置）。"""
    webhook = _env("WECOM_WEBHOOK", "WECHAT_WEBHOOK", "QYWX_WEBHOOK", "PUSH_WEBHOOK_URL", scope=scope)
    if not webhook:
        return None

    payload = {
        "msgtype": "text",
        "text": {"content": _truncate_bytes(f"{title}\n\n{content}", WECOM_TEXT_BYTES_LIMIT)},
    }
    result = _request("POST", webhook, json=payload)
    if result.get("errcode") != 0:
        raise RuntimeError(f"企业微信机器人返回失败: {result}")
    return True


def send_wecom_app(title, content, scope=None):
    """推送到企业微信自建应用，默认发给应用可见范围内全部成员。"""
    corp_id = _env("WECOM_CORPID", "QYWX_CORPID", scope=scope)
    corp_secret = _env("WECOM_CORPSECRET", "QYWX_CORPSECRET", scope=scope)
    agent_id = _env("WECOM_AGENTID", "QYWX_AGENTID", scope=scope)
    if not (corp_id and corp_secret and agent_id):
        return None

    token_result = _request(
        "GET", f"{WECOM_API}/gettoken", params={"corpid": corp_id, "corpsecret": corp_secret}
    )
    access_token = token_result.get("access_token")
    if not access_token:
        raise RuntimeError(f"企业微信获取 access_token 失败: {token_result}")

    payload = {
        "touser": _env("WECOM_TOUSER", "QYWX_TOUSER", default="@all", scope=scope),
        "msgtype": "text",
        "agentid": int(agent_id),
        "text": {"content": _truncate_bytes(f"{title}\n\n{content}", WECOM_TEXT_BYTES_LIMIT)},
        "duplicate_check_interval": 600,
    }
    result = _request(
        "POST", f"{WECOM_API}/message/send", params={"access_token": access_token}, json=payload
    )
    if result.get("errcode") != 0:
        raise RuntimeError(f"企业微信应用消息返回失败: {result}")
    return True


def send_bark(title, content, scope=None):
    """推送到 Bark（iOS）。

    BARK_URL 可填 https://服务器地址/设备Key（自建服务器同格式）或纯设备 Key。
    """
    raw = _env("BARK_URL", scope=scope)
    if not raw:
        return None

    if "://" in raw:
        server, _, device_key = raw.rstrip("/").rpartition("/")
        if not server or not device_key:
            raise RuntimeError("BARK_URL 需形如 https://api.day.app/设备Key")
    else:
        server, device_key = BARK_DEFAULT_SERVER, raw

    payload = {
        "device_key": device_key,
        "title": _truncate_chars(title, TEXT_LIMIT),
        "body": _truncate_chars(content, TEXT_LIMIT),
    }
    result = _request("POST", f"{server}/push", json=payload)
    if result.get("code") != 200:
        raise RuntimeError(f"Bark 返回失败: {result}")
    return True


def send_serverchan(title, content, scope=None):
    """推送到 Server酱。

    SERVERCHAN_KEY 支持两种 Key，按前缀自动识别：
    - sctp…t 开头为 Server酱³（推送到 https://{id}.push.ft07.com）
    - 其余按 Turbo 版（https://sctapi.ftqq.com）
    """
    key = _env("SERVERCHAN_KEY", "SERVERCHAN_SENDKEY", scope=scope)
    if not key:
        return None

    match = re.match(r"^sctp(\d+)t", key)
    if match:
        url = f"https://{match.group(1)}.push.ft07.com/send/{key}.send"
    else:
        url = f"{SERVERCHAN_TURBO_API}/{key}.send"

    data = {
        "title": _truncate_chars(title, TEXT_LIMIT),
        "desp": _truncate_chars(content, TEXT_LIMIT),
    }
    result = _request("POST", url, data=data)
    if result.get("code") != 0:
        raise RuntimeError(f"Server酱返回失败: {result}")
    return True


def send_pushplus(title, content, scope=None):
    """推送到 PushPlus。PUSHPLUS_TOPIC 可选（一对一推送时留空）。"""
    token = _env("PUSHPLUS_TOKEN", "PUSHPLUS_KEY", scope=scope)
    if not token:
        return None

    payload = {
        "token": token,
        "title": _truncate_chars(title, TEXT_LIMIT),
        "content": _truncate_chars(content, TEXT_LIMIT),
        "template": "txt",
    }
    topic = _env("PUSHPLUS_TOPIC", scope=scope)
    if topic:
        payload["topic"] = topic

    result = _request("POST", PUSHPLUS_API, json=payload)
    if result.get("code") != 200:
        raise RuntimeError(f"PushPlus 返回失败: {result}")
    return True


def send_ntfy(title, content, scope=None):
    """推送到 ntfy。NTFY_TOPIC 必填；NTFY_URL 默认官方服务，可指向自建实例。"""
    topic = _env("NTFY_TOPIC", scope=scope)
    if not topic:
        return None

    server = _env("NTFY_URL", default=NTFY_DEFAULT_SERVER, scope=scope).rstrip("/")
    # HTTP 头仅允许 ASCII，标题按 RFC 2047 编码（ntfy 官方支持该形式）
    encoded_title = "=?utf-8?B?" + base64.b64encode(title.encode("utf-8")).decode("utf-8") + "?="
    headers = {"Title": encoded_title, "Priority": _env("NTFY_PRIORITY", default="3", scope=scope)}
    _request(
        "POST",
        f"{server}/{topic}",
        data=_truncate_bytes(content, NTFY_TEXT_BYTES_LIMIT).encode("utf-8"),
        headers=headers,
    )
    return True


def send_gotify(title, content, scope=None):
    """推送到 Gotify（自建服务）。GOTIFY_URL 与 GOTIFY_TOKEN 均必填。"""
    server = _env("GOTIFY_URL", scope=scope)
    token = _env("GOTIFY_TOKEN", scope=scope)
    if not (server and token):
        return None

    try:
        priority = int(_env("GOTIFY_PRIORITY", default="3", scope=scope))
    except ValueError:
        priority = 3

    payload = {
        "title": _truncate_chars(title, TEXT_LIMIT),
        "message": _truncate_chars(content, TEXT_LIMIT),
        "priority": priority,
    }
    result = _request("POST", f"{server.rstrip('/')}/message?token={token}", json=payload)
    if not result.get("id"):
        raise RuntimeError(f"Gotify 返回失败: {result}")
    return True


CHANNELS = (
    ("Telegram", send_telegram),
    ("飞书", send_feishu),
    ("钉钉", send_dingtalk),
    ("企业微信机器人", send_wecom_webhook),
    ("企业微信应用", send_wecom_app),
    ("Bark", send_bark),
    ("Server酱", send_serverchan),
    ("PushPlus", send_pushplus),
    ("ntfy", send_ntfy),
    ("Gotify", send_gotify),
)


def send(title, content, scope=None):
    """向所有已配置渠道推送，返回 {渠道名: "sent"/"skipped"/错误信息}。

    scope 为站点前缀（如 "GLADOS"），启用站点级变量覆盖；留空只用全局变量。
    """
    results = {}
    for name, sender in CHANNELS:
        try:
            sent = sender(title, content, scope)
        except Exception as error:
            results[name] = f"失败: {error}"
            print(f"[通知] {name} 推送失败: {error}")
            continue

        if sent is None:
            results[name] = "skipped"
        else:
            results[name] = "sent"
            print(f"[通知] {name} 推送成功")

    if all(state == "skipped" for state in results.values()):
        print("[通知] 未配置任何推送渠道，跳过通知")
    return results
