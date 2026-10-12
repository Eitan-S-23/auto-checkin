"""多面板（GLaDOS 系）自动签到。

面板列表驱动：哪个面板配置了账号变量就签到哪个，未配置的自动跳过、不算失败。
账号通过各面板的 *_ACCOUNTS_JSON 环境变量提供（JSON 数组，元素含 email / cookie）。
通知走仓库根目录的统一通知模块 notify.py，向所有已配置的渠道推送。
"""
import datetime
import json
import os
import random
import sys
import time

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
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

# 面板配置：accounts_env 为空即视为未启用该面板，静默跳过
PANELS = [
    {
        "name": "GLaDOS",
        "origin": "https://glados.cloud",
        "token": "glados.cloud",
        "accounts_env": "GLADOS_ACCOUNTS_JSON",
        "ua_env": "GLADOS_USER_AGENT",
    },
    {
        "name": "Railgun",
        "origin": "https://railgun.info",
        "token": "railgun.info",
        "accounts_env": "RAILGUN_ACCOUNTS_JSON",
        "ua_env": "RAILGUN_USER_AGENT",
    },
]


def parse_accounts(raw):
    """解析账号 JSON，兼容双重编码与列表中元素为 JSON 字符串的情况。"""
    accounts = json.loads(raw)
    if isinstance(accounts, str):
        accounts = json.loads(accounts)
    return [json.loads(item) if isinstance(item, str) else item for item in accounts]


def translate_message(raw_message):
    if raw_message == "Please Try Tomorrow":
        return "签到失败，请明天再试 🤖"
    elif "Checkin! Got" in raw_message:
        points = raw_message.split("Got ")[1].split(" Points")[0]
        return f"签到成功，获得{points}积分 🎉"
    elif raw_message == "Checkin Repeats! Please Try Tomorrow":
        return "重复签到，请明天再试 🔁"
    elif raw_message == "Today's observation logged. Return tomorrow for more points.":
        return "今日已签到 ✅"
    elif "please checkin via" in raw_message:
        return "签到失败，请更新Cookie ⚠️"
    else:
        return f"未知结果: {raw_message} ❓"


def sanitize_header_value(value):
    if isinstance(value, str):
        try:
            value.encode('latin-1')
            return value
        except UnicodeEncodeError:
            return value.encode('latin-1', 'replace').decode('latin-1')
    return value


def generate_headers(cookie, panel):
    ua_env = panel.get("ua_env")
    user_agent = (os.getenv(ua_env, "") if ua_env else "").strip() or DEFAULT_USER_AGENT
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate, br",
        "Content-Type": "application/json;charset=UTF-8",
        "Cookie": cookie,
        "Origin": panel["origin"],
        "User-Agent": user_agent,
    }
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


def create_retry_session():
    return cffi_requests.Session(impersonate="chrome124")


def check_account_status(panel, cookie):
    url = f"{panel['origin']}/api/user/status"
    headers = generate_headers(cookie, panel)
    try:
        r = create_retry_session().get(url, headers=headers, timeout=15)
        data = r.json()
        days = format_days(data['data']['leftDays'])
        traffic = data['data'].get('traffic', 0)
        return days, bytes_to_gb(traffic)
    except Exception as e:
        print("状态查询异常:", e)
        return "获取失败", "获取失败"


def fetch_points(panel, cookie):
    url = f"{panel['origin']}/api/user/points"
    headers = generate_headers(cookie, panel)
    try:
        data = create_retry_session().get(url, headers=headers, timeout=15).json()
        points = data.get('points', 0)
        return str(int(float(points)))
    except Exception:
        return "0"


def sign(panel, cookie):
    url = f"{panel['origin']}/api/user/checkin"
    headers = generate_headers(cookie, panel)
    data = {"token": panel["token"]}
    try:
        r = create_retry_session().post(url, headers=headers, json=data, timeout=15)
        msg = r.json().get("message", "")
        return translate_message(msg)
    except Exception as e:
        return f"请求异常: {str(e)[:30]}"


def checkin_panel(panel):
    """处理单个面板并返回推送内容 (标题, 正文)；面板未启用时返回 None。"""
    raw = os.getenv(panel["accounts_env"], "").strip()
    if not raw:
        print(f"[{panel['name']}] 未配置 {panel['accounts_env']}，跳过")
        return None

    beijing_now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    title = f"【{panel['name']} 自动签到】{beijing_now.strftime('%m-%d %H:%M')}"

    try:
        accounts = parse_accounts(raw)
    except Exception as e:
        print(f"[{panel['name']}] 账号配置解析失败: {e}")
        return title, f"账号配置解析失败，请检查 {panel['accounts_env']} ⚠️"

    if not accounts:
        print(f"[{panel['name']}] 账号列表为空，跳过")
        return None

    msg_list = []
    for idx, acc in enumerate(accounts, 1):
        email = acc.get("email", f"账号{idx}")
        cookie = acc.get("cookie", "")
        if not cookie:
            msg_list.append(f"{email} | 无Cookie")
            continue

        print(f"签到: {email}")
        sign_msg = sign(panel, cookie)
        left_days, traffic = check_account_status(panel, cookie)
        point = fetch_points(panel, cookie)

        msg_list.append(f"📩 账号：{email}")
        msg_list.append(f"✅ 签到：{sign_msg}")
        msg_list.append(f"📅 剩余：{left_days} 天")
        msg_list.append(f"🎯 积分：{point}")
        msg_list.append(f"📶 流量已用：{traffic} GB")
        msg_list.append("-" * 20)

        time.sleep(random.randint(2, 4))

    return title, "\n".join(msg_list)


def run():
    processed = 0
    for panel in PANELS:
        result = checkin_panel(panel)
        if result is None:
            continue
        processed += 1
        title, content = result
        notify.send(title, content)

    if processed == 0:
        print("所有面板均未配置，无签到任务")


if __name__ == "__main__":
    print("===== 开始签到 =====")
    run()
    print("===== 执行完成 =====")
