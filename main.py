import requests
from curl_cffi import requests as cffi_requests
import datetime
import random
import time
import os

# 从 GitHub Secrets 读取配置
PUSH_WEBHOOK_URL = os.getenv("PUSH_WEBHOOK_URL", "")
ACCOUNTS_JSON = os.getenv("ACCOUNTS_JSON", "[]")

# 代理（GitHub 服务器在国外，无需代理）
PROXY_CONFIG = None

# 解析账号
import json
ACCOUNTS = json.loads(ACCOUNTS_JSON)

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

def generate_headers(cookie):
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate, br",
        "Content-Type": "application/json;charset=UTF-8",
        "Cookie": cookie,
        "Origin": "https://glados.cloud",
        # 固定 Windows UA：GLaDOS 会比对签到请求客户端与登录设备，随机切换到 macOS 会触发反自动化检测导致积分被拒
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    }
    return {k: sanitize_header_value(v) for k, v in headers.items()}

def format_days(days_str):
    try:
        days = float(days_str)
        return str(int(days)) if days.is_integer() else f"{days:.2f}"
    except:
        return days_str

def bytes_to_gb(traffic_bytes):
    """字节转 GB，保留3位小数"""
    if not traffic_bytes:
        return "0.000"
    gb = int(traffic_bytes) / 1024 / 1024 / 1024
    return f"{gb:.3f}"

def send_webhook(content):
    if not PUSH_WEBHOOK_URL:
        print("未配置 Webhook，跳过推送")
        return
    try:
        data = {"msgtype": "text", "text": {"content": content}}
        requests.post(PUSH_WEBHOOK_URL, json=data, timeout=10)
        print("Webhook 发送成功")
    except Exception as e:
        print(f"发送失败: {e}")

def create_retry_session():
    return cffi_requests.Session(impersonate="chrome124")

# ================ 新版：查询 天数 + 流量 ================
def check_account_status(email, cookie):
    url = "https://glados.cloud/api/user/status"
    headers = generate_headers(cookie)
    try:
        r = create_retry_session().get(url, headers=headers, timeout=15)
        data = r.json()
        days = format_days(data['data']['leftDays'])
        traffic = data['data'].get('traffic', 0)
        traffic_gb = bytes_to_gb(traffic)
        return days, traffic_gb
    except Exception as e:
        print("状态查询异常:", e)
        return "获取失败", "获取失败"

# ================ 新版：查询积分（只显示整数） ================
def fetch_points(cookie):
    url = "https://glados.cloud/api/user/points"
    headers = generate_headers(cookie)
    try:
        data = create_retry_session().get(url, headers=headers, timeout=15).json()
        points = data.get('points', 0)
        return str(int(float(points)))
    except:
        return "0"

def sign(email, cookie):
    url = "https://glados.cloud/api/user/checkin"
    headers = generate_headers(cookie)
    data = {"token": "glados.cloud"}
    try:
        r = create_retry_session().post(url, headers=headers, json=data, timeout=15)
        msg = r.json().get("message", "")
        return translate_message(msg)
    except Exception as e:
        return f"请求异常: {str(e)[:30]}"

def run():
    if not ACCOUNTS:
        print("未配置任何账号")
        return

    msg_list = []
    beijing_time = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    now = beijing_time.strftime("%m-%d %H:%M")
    msg_list.append(f"【GLaDOS 自动签到】{now}\n")

    for idx, acc in enumerate(ACCOUNTS, 1):
        email = acc.get("email", f"账号{idx}")
        cookie = acc.get("cookie", "")
        if not cookie:
            msg_list.append(f"{email} | 无Cookie")
            continue

        print(f"签到: {email}")
        sign_msg = sign(email, cookie)
        left_days, traffic = check_account_status(email, cookie)
        point = fetch_points(cookie)
        
        # 排版：每项单独一行
        msg_list.append(f"📩 账号：{email}")
        msg_list.append(f"✅ 签到：{sign_msg}")
        msg_list.append(f"📅 剩余：{left_days} 天")
        msg_list.append(f"🎯 积分：{point}")
        msg_list.append(f"📶 流量已用：{traffic} GB")
        msg_list.append("-" * 20)  # 分隔线
        
        time.sleep(random.randint(2, 4))

    final_msg = "\n".join(msg_list)
    print("\n" + final_msg)
    send_webhook(final_msg)

if __name__ == "__main__":
    print("===== 开始签到 =====")
    run()
    print("===== 执行完成 =====")
