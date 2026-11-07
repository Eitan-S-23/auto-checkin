"""多面板（GLaDOS 系）自动签到。

面板列表驱动：哪个面板配置了账号变量就签到哪个，未配置的自动跳过、不算失败。
账号通过各面板的 *_ACCOUNTS_JSON 环境变量提供（JSON 数组，元素含 email / cookie）。
通知走仓库根目录的统一通知模块 notify.py，向所有已配置的渠道推送。
"""
import argparse
import datetime
import hashlib
import http.cookiejar
import json
import math
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import get_args
from urllib.parse import urlparse
from urllib.request import Request

from curl_cffi import requests as cffi_requests

# 通知模块位于仓库根目录 notify.py，显式加入搜索路径，
# 保证从任意工作目录运行脚本都能正确导入
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import notify

# 固定 Windows UA，不做随机轮换：GLaDOS 系面板会比对签到请求客户端与登录设备平台，
# UA 与登录设备不一致会被反自动化检测拦截、积分被拒（2026-10 实测）。
# 如某站需要以其他设备登录，可用面板级 *_USER_AGENT 变量单独覆盖。
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

RECORDED_CHECKIN_MESSAGES = {
    "Checkin Repeats! Please Try Tomorrow": "重复签到，请明天再试",
    "Today's observation logged. Return tomorrow for more points.": "今日已签到",
    "Already checked in today. Come back tomorrow.": "今日已签到",
}

# 面板配置：accounts_env 为空即视为未启用该面板，静默跳过。
# notify_scope 为通知站点前缀：配了 GLADOS_/RAILGUN_ 开头的渠道变量时
# 该面板走自己的推送地址，未配则回退全局渠道变量（见仓库根 notify.py）。
PANELS = [
    {
        "name": "GLaDOS",
        "origin": "https://glados.cloud",
        "token": "glados.cloud",
        "accounts_env": "GLADOS_ACCOUNTS_JSON",
        "ua_env": "GLADOS_USER_AGENT",
        "notify_scope": "GLADOS",
        "checkin_challenge": True,
    },
    {
        "name": "Railgun",
        "origin": "https://railgun.info",
        "token": "railgun.info",
        "accounts_env": "RAILGUN_ACCOUNTS_JSON",
        "ua_env": "RAILGUN_USER_AGENT",
        "notify_scope": "RAILGUN",
    },
]


def parse_accounts(raw):
    """解析账号 JSON，兼容双重编码与列表中元素为 JSON 字符串的情况。"""
    accounts = json.loads(raw)
    if isinstance(accounts, str):
        accounts = json.loads(accounts)
    if not isinstance(accounts, list):
        raise ValueError("账号配置必须是 JSON 数组")
    result = []
    for item in accounts:
        if isinstance(item, str):
            try:
                item = json.loads(item)
            except ValueError:
                pass
        result.append(item)
    return result


def translate_message(raw_message):
    if not isinstance(raw_message, str):
        return "未知结果，请核对官网"
    if raw_message == "Please Try Tomorrow":
        return "签到失败，请明天再试"
    elif match := re.search(r"Checkin!\s*Got\s+(\d+(?:\.\d+)?)\s+Points?\b", raw_message, re.I):
        return f"签到成功，获得{match.group(1)}积分"
    elif raw_message in RECORDED_CHECKIN_MESSAGES:
        return RECORDED_CHECKIN_MESSAGES[raw_message]
    elif "please checkin via" in raw_message:
        return "签到失败，请更新Cookie"
    else:
        return "未知结果，请核对官网"


def sanitize_header_value(value):
    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise PanelRequestError("请求头格式无效，请检查 Cookie 和 User-Agent", kind="config")
    try:
        value.encode("latin-1")
    except UnicodeEncodeError:
        raise PanelRequestError("请求头包含无法发送的字符，请重新复制原始请求头", kind="config") from None
    return value


def generate_headers(cookie, panel, user_agent=None):
    ua_env = panel.get("ua_env")
    if user_agent is None:
        user_agent = (os.getenv(ua_env, "") if ua_env else "").strip() or DEFAULT_USER_AGENT
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate, br",
        "Content-Type": "application/json;charset=UTF-8",
        "Cookie": cookie,
        "Origin": panel["origin"],
        "Referer": panel["origin"] + "/console/checkin",
        "User-Agent": user_agent,
    }
    chrome = re.search(r"(?:Chrome|Chromium)/(\d+)", user_agent)
    if chrome:
        platform = "Unknown"
        for marker, name in (("Android", "Android"), ("Macintosh", "macOS"), ("Windows", "Windows"), ("Linux", "Linux")):
            if marker in user_agent:
                platform = name
                break
        brand = "Microsoft Edge" if "Edg/" in user_agent else "Google Chrome"
        headers.update({
            "Sec-CH-UA": f'"Chromium";v="{chrome.group(1)}", "{brand}";v="{chrome.group(1)}", "Not_A Brand";v="99"',
            "Sec-CH-UA-Mobile": "?1" if "Mobile" in user_agent else "?0",
            "Sec-CH-UA-Platform": f'"{platform}"',
        })
    return {k: sanitize_header_value(v) for k, v in headers.items()}


def format_days(days_str):
    try:
        days = float(days_str)
        return str(int(days)) if days.is_integer() else f"{days:.2f}"
    except Exception:
        return days_str


def bytes_to_gb(traffic_bytes):
    """字节转 GB，保留 3 位小数"""
    if not traffic_bytes:
        return "0.000"
    gb = int(traffic_bytes) / 1024 / 1024 / 1024
    return f"{gb:.3f}"


def create_retry_session(impersonate="chrome124"):
    return cffi_requests.Session(impersonate=impersonate)


RETRYABLE_HTTP_STATUS = {408, 429, 500, 502, 503, 504}
# curl transport errors only; certificate, authentication and configuration errors are excluded.
RETRYABLE_CURL_CODES = {5, 6, 7, 16, 18, 28, 52, 55, 56, 92}
MAX_RETRY_WAIT_SECONDS = 60
CHECKIN_CHALLENGE_DELAY_SECONDS = 2.7
CHECKIN_CHALLENGE_SUFFIX = ".glados-observe-v1"
CHECKIN_CHALLENGE_DEFAULT_TTL_MS = 300000


class PanelRequestError(RuntimeError):
    def __init__(self, message, retryable=False, retry_after=None, kind="request"):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after
        self.kind = kind


def cookie_pairs(raw):
    if not isinstance(raw, str):
        raise PanelRequestError("cookie 必须是请求头字符串", kind="config")
    raw = re.sub(r"^cookie\s*:\s*", "", raw.strip(), flags=re.I)
    sanitize_header_value(raw)
    pairs = {}
    for part in raw.split(";"):
        name, separator, value = part.strip().partition("=")
        if not separator or not name or re.search(r"[\s,]", name) or name in pairs:
            raise PanelRequestError("Cookie 字段格式无效或重复，请复制完整请求头", kind="config")
        pairs[name] = value
    for name, value in pairs.items():
        if re.fullmatch(r"[A-Za-z0-9_.-]+:sess", name) and (not value or not pairs.get(name + ".sig")):
            raise PanelRequestError("会话 Cookie 或签名缺失，请复制同一会话的完整字段", kind="config")
        if name.endswith(":sess.sig") and not pairs.get(name[:-4]):
            raise PanelRequestError("会话 Cookie 或签名缺失，请复制同一会话的完整字段", kind="config")
    return pairs


def browser_profile(user_agent, explicit=None):
    profiles = set(get_args(getattr(cffi_requests, "BrowserTypeLiteral", None)))
    if not profiles:
        profiles = {item.value for item in getattr(cffi_requests, "BrowserType", ())}
    profiles = {item for item in profiles if isinstance(item, str)} or {"chrome124"}
    if explicit:
        if not isinstance(explicit, str):
            raise PanelRequestError("impersonate 必须是字符串", kind="config")
        if explicit not in profiles:
            raise PanelRequestError("impersonate 不是当前 curl_cffi 支持的预设", kind="config")
        return explicit, False
    for pattern, family in ((r"Edg/(\d+)", "edge"), (r"Firefox/(\d+)", "firefox"), (r"(?:Chrome|Chromium)/(\d+)", "chrome")):
        match = re.search(pattern, user_agent)
        if match:
            major = int(match.group(1))
            mobile = family == "chrome" and "Android" in user_agent
            candidates = []
            for profile in profiles:
                version = re.fullmatch(family + r"(\d+)(a|_android|_macos)?", profile)
                if version and ("_android" in profile) == mobile:
                    candidates.append((abs(int(version.group(1)) - major), int(version.group(1)), profile))
            if candidates:
                distance, _, profile = min(candidates)
                return profile, distance == 0
            break
    if "Safari/" in user_agent and "Chrome/" not in user_agent:
        version = re.search(r"Version/(\d+)", user_agent)
        ios = "iPhone" in user_agent or "iPad" in user_agent
        candidates = [(abs(int(item[6:8]) - int(version.group(1))), item) for item in profiles
                      if version and re.fullmatch(r"safari\d+(?:_ios)?", item) and ("_ios" in item) == ios]
        if candidates:
            distance, profile = min(candidates)
            # Safari presets encode minor/patch versions too; a major match is approximate.
            return profile, False
    return "chrome124", False


class AccountCookieJar(http.cookiejar.CookieJar):
    """Treat Domain=host and Domain=.host as the same storage key."""

    def set_cookie(self, cookie):
        cookie.domain = cookie.domain.lstrip(".")
        super().set_cookie(cookie)

    def clear(self, domain=None, path=None, name=None):
        super().clear(domain.lstrip(".") if domain else domain, path, name)


class AccountClient:
    def __init__(self, panel, cookie, user_agent=None, impersonate=None):
        self.panel = panel
        self.user_agent = user_agent or (os.getenv(panel.get("ua_env", ""), "").strip() if panel.get("ua_env") else "") or DEFAULT_USER_AGENT
        sanitize_header_value(self.user_agent)
        pairs = cookie_pairs(cookie)
        self.profile, self.profile_exact = browser_profile(self.user_agent, impersonate)
        self.jar = AccountCookieJar()
        self.required_sessions = {name for name in pairs if name.endswith(":sess")}
        host = urlparse(panel["origin"]).hostname
        for name, value in pairs.items():
            self.jar.set_cookie(http.cookiejar.Cookie(0, name, value, None, False, host, False, False,
                "/", True, panel["origin"].startswith("https://"), None, True, None, None, {}, False))
        self.cookie_updated = False
        try:
            self.session = create_retry_session(self.profile)
        except Exception as error:
            raise PanelRequestError(f"Session 初始化失败（{type(error).__name__}）", kind="config") from None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.session.close()

    def headers(self, path):
        request = Request(self.panel["origin"] + path)
        self.jar.add_cookie_header(request)
        cookie = request.get_header("Cookie", "")
        active = dict(part.strip().partition("=")[::2] for part in cookie.split(";") if "=" in part)
        if any(not active.get(name) or not active.get(name + ".sig") for name in self.required_sessions):
            raise PanelRequestError("服务端清除了登录会话，请重新登录，不继续请求", kind="auth")
        return generate_headers(cookie, self.panel, self.user_agent)

    def merge_cookies(self, response, path):
        headers = response.headers
        values = headers.get_list("Set-Cookie") if hasattr(headers, "get_list") else None
        if not isinstance(values, (list, tuple)):
            values = [value for key, value in headers.items() if key.lower() == "set-cookie"] if isinstance(headers, dict) else []
        message = Message()
        for value in values:
            if isinstance(value, str):
                for field in re.split(r",(?=\s*[A-Za-z0-9_.:-]+=)", value):
                    message.add_header("Set-Cookie", field)
        if not message.get_all("Set-Cookie"):
            return
        before = {(c.domain, c.path, c.name, c.value, c.expires) for c in self.jar}
        adapter = type("CookieResponse", (), {"info": lambda _: message})()
        self.jar.extract_cookies(adapter, Request(self.panel["origin"] + path))
        after = {(c.domain, c.path, c.name, c.value, c.expires) for c in self.jar}
        self.cookie_updated = self.cookie_updated or before != after
        self.headers(path)


@dataclass(frozen=True)
class AccountResult:
    success: bool
    message: str
    left_days: str = "未获取"
    traffic: str = "未获取"
    points: str = "未获取"
    cookie_updated: bool = False


@dataclass(frozen=True)
class PanelResult:
    title: str
    content: str
    success: bool

    def __iter__(self):
        return iter((self.title, self.content))


def retry_settings():
    def bounded_int(name, default, minimum, maximum):
        try:
            value = int(os.getenv(name, ""))
            return value if minimum <= value <= maximum else default
        except ValueError:
            return default

    return (
        bounded_int("PANEL_MAX_ATTEMPTS", 3, 1, 5),
        bounded_int("PANEL_RETRY_DELAY_SECONDS", 10, 0, 60),
    )


def retry_after_seconds(value):
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    try:
        if value.isdecimal():
            return int(value)
        timestamp = parsedate_to_datetime(value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=datetime.timezone.utc)
        return max(0, timestamp.timestamp() - time.time())
    except (ValueError, TypeError, OverflowError):
        return None


def request_json_once(panel, cookie, path, method, payload, client=None):
    if client is None:
        with AccountClient(panel, cookie) as owned:
            return request_json_once(panel, cookie, path, method, payload, owned)
    try:
        kwargs = {"headers": client.headers(path), "timeout": 15, "allow_redirects": False,
                  "discard_cookies": True, "default_headers": False}
        if method == "POST":
            kwargs["json"] = payload
        response = getattr(client.session, method.lower())(panel["origin"] + path, **kwargs)
        status = response.status_code
        if status in (401, 403):
            raise PanelRequestError(f"HTTP {status}，认证或访问被拒绝，不重试", kind="auth")
        if status in RETRYABLE_HTTP_STATUS:
            raise PanelRequestError(
                f"HTTP {status}", retryable=True,
                retry_after=retry_after_seconds(response.headers.get("Retry-After")), kind="http",
            )
        if not 200 <= status < 300:
            raise PanelRequestError(f"HTTP {status}，不重试", kind="http")
        try:
            data = response.json()
        except ValueError:
            raise PanelRequestError("响应不是有效 JSON，状态未知，不重试", kind="response") from None
        if not isinstance(data, dict):
            raise PanelRequestError("响应 JSON 结构异常，状态未知，不重试", kind="response")
        message = data.get("message", "")
        message = message.lower() if isinstance(message, str) else ""
        if data.get("reason") == "device-mismatch" or "automated check-in detected" in message:
            raise PanelRequestError("设备校验被拒绝，请核对 Cookie 和 User-Agent，不重试", kind="device")
        if data.get("code") in (-2, "-2") or any(marker in message for marker in (
            "没有权限", "unauthorized", "permission denied", "forbidden",
        )):
            raise PanelRequestError("认证被拒绝，请核对完整 Cookie 和登录状态，不重试", kind="auth")
        client.merge_cookies(response, path)
        return data
    except PanelRequestError:
        raise
    except Exception as error:
        retryable = isinstance(error, (TimeoutError, ConnectionError)) or getattr(error, "code", None) in RETRYABLE_CURL_CODES
        # Transport exceptions can contain Cookie values; retain only their type.
        raise PanelRequestError(f"请求异常（{type(error).__name__}）", retryable=retryable, kind="transport") from None


def request_json(panel, cookie, path, method="GET", payload=None, client=None, *, payload_factory=None):
    """Build per-attempt payloads inside the retry loop, on the same account Session."""
    if client is None:
        with AccountClient(panel, cookie) as owned:
            return request_json(panel, cookie, path, method, payload, owned, payload_factory=payload_factory)
    attempts, base_delay = retry_settings()
    for attempt in range(1, attempts + 1):
        try:
            attempt_payload = payload_factory(client) if payload_factory is not None else payload
            return request_json_once(panel, cookie, path, method, attempt_payload, client)
        except PanelRequestError as error:
            if not error.retryable:
                raise
            if attempt == attempts:
                raise PanelRequestError(f"{error}，已尝试 {attempt} 次，状态未知", kind=error.kind) from None
            delay = min(base_delay * 2 ** (attempt - 1), MAX_RETRY_WAIT_SECONDS)
            if error.retry_after is not None:
                if error.retry_after > MAX_RETRY_WAIT_SECONDS:
                    raise PanelRequestError(
                        f"{error}，服务端要求等待超过 {MAX_RETRY_WAIT_SECONDS} 秒，停止本次重试", kind=error.kind,
                    ) from None
                delay = max(delay, error.retry_after)
            print(f"[{panel['name']}] {path} {error}，{delay:g} 秒后重试（{attempt + 1}/{attempts}）")
            time.sleep(delay)


def check_account_status(panel, cookie, client=None):
    try:
        data = request_json(panel, cookie, "/api/user/status", client=client)
        if data.get("code") not in (None, 0, "0") or not isinstance(data.get("data"), dict):
            raise PanelRequestError("状态接口未返回有效账号数据", kind="response")
        if isinstance(data["data"]["leftDays"], bool) or not math.isfinite(float(data["data"]["leftDays"])):
            raise ValueError("Invalid days")
        days = format_days(data['data']['leftDays'])
        traffic = data['data'].get('traffic', 0)
        if isinstance(traffic, bool) or not math.isfinite(float(traffic)) or float(traffic) < 0:
            raise ValueError("Invalid traffic")
        return days, bytes_to_gb(traffic)
    except PanelRequestError as error:
        if client is not None:
            raise
        print("状态查询失败:", error)
        return "获取失败", "获取失败"
    except Exception as e:
        if client is not None:
            raise PanelRequestError("账号状态数据不完整或格式异常", kind="response") from None
        print("状态查询异常:", type(e).__name__)
        return "获取失败", "获取失败"


def fetch_points(panel, cookie, client=None):
    try:
        data = request_json(panel, cookie, "/api/user/points", client=client)
        if data.get("code") not in (None, 0, "0"):
            raise PanelRequestError("积分接口业务失败", kind="business")
        nested = data.get("data")
        points = data.get("points") if "points" in data else nested.get("points") if isinstance(nested, dict) else None
        if isinstance(points, bool) or not math.isfinite(float(points)) or float(points) < 0:
            raise ValueError("Invalid points")
        return str(int(float(points)))
    except PanelRequestError:
        if client is not None:
            raise
        return "获取失败"
    except Exception:
        if client is not None:
            raise PanelRequestError("积分数据不完整或格式异常", kind="response") from None
        return "获取失败"


def prepare_checkin_payload(panel, cookie, client):
    started_at = time.monotonic()
    challenge = request_json_once(panel, cookie, "/api/user/checkin/challenge", "GET", None, client)
    code = challenge.get("code")
    if type(code) not in (int, str) or code not in (0, "0"):
        raise PanelRequestError("签到挑战接口业务失败，未提交签到", kind="challenge")
    nonce = challenge.get("nonce")
    ttl = challenge.get("ttl", CHECKIN_CHALLENGE_DEFAULT_TTL_MS)
    if not isinstance(nonce, str) or not nonce.strip() or len(nonce) > 4096:
        raise PanelRequestError("签到挑战 nonce 缺失或格式异常，未提交签到", kind="challenge")
    if (type(ttl) not in (int, float) or (type(ttl) is float and not math.isfinite(ttl)) or
            ttl <= CHECKIN_CHALLENGE_DELAY_SECONDS * 1000):
        raise PanelRequestError("签到挑战 TTL 无效或不足，未提交签到", kind="challenge")
    try:
        proof = hashlib.sha256((nonce + CHECKIN_CHALLENGE_SUFFIX).encode("utf-8")).hexdigest()
    except UnicodeError:
        raise PanelRequestError("签到挑战 nonce 编码无效，未提交签到", kind="challenge") from None
    if (time.monotonic() - started_at + CHECKIN_CHALLENGE_DELAY_SECONDS) * 1000 >= ttl:
        raise PanelRequestError("签到挑战已过期或剩余时间不足，未提交签到", retryable=True, kind="challenge")
    time.sleep(CHECKIN_CHALLENGE_DELAY_SECONDS)
    if (time.monotonic() - started_at) * 1000 >= ttl:
        raise PanelRequestError("签到挑战已过期，未提交签到", retryable=True, kind="challenge")
    return {"token": panel["token"], "nonce": nonce, "proof": proof}


def sign(panel, cookie, client=None):
    try:
        kwargs = {"client": client}
        if panel.get("checkin_challenge"):
            kwargs["payload_factory"] = lambda active: prepare_checkin_payload(panel, cookie, active)
        data = request_json(panel, cookie, "/api/user/checkin", "POST", {"token": panel["token"]}, **kwargs)
        msg = data.get("message", "")
        code = data.get("code")
        normal = isinstance(msg, str) and re.fullmatch(r"Checkin!\s*Got\s+\d+(?:\.\d+)?\s+Points?[.!]?", msg.strip(), re.I)
        free_account = panel.get("name") == "Railgun" and isinstance(msg, str) and re.fullmatch(
            r"Checkin!\s*Got\s+\d+(?:\.\d+)?\s+Points?\s+\(Free account - upgrade for more!\)", msg.strip(), re.I,
        )
        repeated = isinstance(msg, str) and msg in RECORDED_CHECKIN_MESSAGES
        if not ((code in (None, 0, "0") and normal) or (not isinstance(code, bool) and code in (0, "0") and free_account) or
                (code in (None, 0, "0", 1, "1") and repeated)):
            raise PanelRequestError("签到接口未返回明确成功或已签到结果，请核对官网", kind="business")
        return translate_message(msg) + ("（免费账号奖励）" if free_account else "")
    except PanelRequestError as error:
        if client is not None:
            raise
        return f"请求失败: {error}"
    except Exception as e:
        if client is not None:
            raise PanelRequestError(f"签到处理异常（{type(e).__name__}）", kind="response") from None
        return f"请求异常: {type(e).__name__}"


def process_account(panel, account, diagnostic=False):
    if not isinstance(account, dict):
        return AccountResult(False, "账号配置项必须是 JSON 对象")
    cookie = account.get("cookie", "")
    if not cookie:
        return AccountResult(False, "无Cookie，请配置完整账号凭据")
    for field in ("user_agent", "impersonate"):
        if account.get(field) is not None and not isinstance(account[field], str):
            return AccountResult(False, f"{field} 必须是字符串")
    message, days, traffic, points = "未执行签到", "未获取", "未获取", "未获取"
    client = None
    try:
        with AccountClient(panel, cookie, account.get("user_agent", "").strip() if account.get("user_agent") else None,
                           account.get("impersonate", "").strip() if account.get("impersonate") else None) as client:
            if not client.profile_exact:
                print(f"[{panel['name']}] UA 无精确对应预设或使用显式预设；保留原 UA，不保证完整浏览器指纹一致")
            if not diagnostic:
                message = sign(panel, cookie, client=client)
            days, traffic = check_account_status(panel, cookie, client=client)
            if diagnostic:
                message = "登录状态有效（仅查询，未执行签到）"
            else:
                points = fetch_points(panel, cookie, client=client)
        return AccountResult(True, message, days, traffic, points, client.cookie_updated)
    except PanelRequestError as error:
        failure = str(error)
    except Exception as error:
        failure = f"账号处理异常（{type(error).__name__}）"
    if message != "未执行签到":
        failure = message + "；后续查询失败：" + failure
    return AccountResult(False, failure, days, traffic, points, bool(client and client.cookie_updated))


def checkin_panel(panel, diagnostic=False):
    """返回含实际成功状态的面板结果；未启用时返回 None。"""
    raw = os.getenv(panel["accounts_env"], "").strip()
    if not raw:
        print(f"[{panel['name']}] 未配置 {panel['accounts_env']}，跳过")
        return None

    beijing_now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    action = "只读诊断" if diagnostic else "自动签到"
    title = f"【{panel['name']} {action}】{beijing_now.strftime('%m-%d %H:%M')}"

    try:
        accounts = parse_accounts(raw)
    except Exception:
        print(f"[{panel['name']}] 账号配置解析失败，请检查 JSON 数组结构")
        return PanelResult(title, f"账号配置解析失败，请检查 {panel['accounts_env']} ⚠️", False)

    if not accounts:
        print(f"[{panel['name']}] 账号列表为空，跳过")
        return None

    msg_list, all_success = [], True
    for idx, acc in enumerate(accounts, 1):
        email = str(acc.get("email", f"账号{idx}")) if isinstance(acc, dict) else f"账号{idx}"
        result = process_account(panel, acc, diagnostic=diagnostic)
        all_success = all_success and result.success
        status = "成功" if result.success else "失败"
        print(f"[{panel['name']}] 账号{idx}: {status}；{result.message}")
        msg_list.append(f"📩 账号：{email}")
        msg_list.append(f"{status}：{result.message}")
        msg_list.append(f"📅 剩余：{result.left_days} 天")
        msg_list.append(f"🎯 积分：{result.points}")
        msg_list.append(f"📶 流量已用：{result.traffic} GB")
        if result.cookie_updated:
            msg_list.append("会话 Cookie 有变化，仅用于本次运行，未写回 Secret")
            print(f"[{panel['name']}] 账号{idx}: 会话 Cookie 有变化，仅保存在内存中")
        msg_list.append("-" * 20)
        if idx < len(accounts):
            time.sleep(random.randint(2, 4))
    return PanelResult(title, "\n".join(msg_list), all_success)


def diagnostic_mode():
    value = os.getenv("PANEL_DIAGNOSTIC_ONLY", "").strip().lower()
    if value in ("", "0", "false", "no", "off"):
        return False
    if value in ("1", "true", "yes", "on"):
        return True
    raise PanelRequestError("PANEL_DIAGNOSTIC_ONLY 格式无效，未执行任务", kind="config")


def run(diagnostic=None):
    try:
        diagnostic = diagnostic_mode() if diagnostic is None else diagnostic
    except PanelRequestError as error:
        print(str(error))
        return 1
    processed = 0
    failed = False
    for panel in PANELS:
        try:
            result = checkin_panel(panel, diagnostic=diagnostic)
        except Exception as error:
            print(f"[{panel['name']}] 任务处理异常（{type(error).__name__}）")
            failed = True
            continue
        if result is None:
            continue
        processed += 1
        failed = failed or not result.success
        if not diagnostic:
            try:
                notifications = notify.send(result.title, result.content, scope=panel["notify_scope"])
                if isinstance(notifications, dict) and any(value not in ("sent", "skipped") for value in notifications.values()):
                    failed = True
            except Exception as error:
                print(f"[{panel['name']}] 通知处理异常（{type(error).__name__}）")
                failed = True
    if processed == 0 and not failed:
        print("所有面板均未配置，无签到任务")
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="GLaDOS / Railgun 签到或只读诊断")
    parser.add_argument("--diagnose", action="store_true", help="仅查询账号状态，不签到、不推送")
    args = parser.parse_args(argv)
    print("===== 开始任务 =====")
    result = run(diagnostic=True if args.diagnose else None)
    print("===== 执行完成 =====")
    return result


if __name__ == "__main__":
    sys.exit(main())
