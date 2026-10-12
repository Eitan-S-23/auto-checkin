# 多站点自动签到集合（GLaDOS / Railgun / NodeSeek / DeepFlood / linux.sb）

把多个每日自动签到任务合并到一个仓库统一管理：**配置了对应变量的服务才签到，没配置的自动跳过。**

## 服务与定时

| 服务 | 脚本 | 定时（北京时间） | 启用条件（配置对应 secret） |
|---|---|---|---|
| GLaDOS | `panels/checkin.py` | 每 8 小时 | `GLADOS_ACCOUNTS_JSON` |
| Railgun | `panels/checkin.py` | 每 8 小时 | `RAILGUN_ACCOUNTS_JSON` |
| NodeSeek | `nodeseek/nodeseek_daily.py` | 每天 08:00 | `NS_COOKIE` |
| DeepFlood | `nodeseek/nodeseek_daily.py` | 每天 08:00 | `DEEPFLOOD_COOKIE` |
| linux.sb | `nodeseek/linuxsb_daily.py` | 每天 08:00 | `LINUXSB_COOKIE` 或 `LINUXSB_ACCOUNT` |

## Workflows

- `panels.yml` —— GLaDOS + Railgun（API 签到，curl_cffi）
- `daily.yml` —— NodeSeek + DeepFlood + linux.sb（浏览器过盾）

## Secrets 清单（账号类）

| 名称 | 用途 |
|---|---|
| `GLADOS_ACCOUNTS_JSON` | GLaDOS 账号 JSON 数组（email + cookie） |
| `RAILGUN_ACCOUNTS_JSON` | Railgun 账号 JSON 数组（email + cookie） |
| `NS_COOKIE` / `DEEPFLOOD_COOKIE` / `LINUXSB_COOKIE` / `LINUXSB_ACCOUNT` / `NS_RANDOM` | 三站签到，详见 [`nodeseek/README.md`](nodeseek/README.md) |

## 通知渠道（全部可选）

所有签到任务（GLaDOS / Railgun / NodeSeek / DeepFlood / linux.sb）共用同一套通知配置：
**配了哪个渠道就推哪个，未配置的自动跳过，任一渠道失败不影响其他渠道。** 实现见根目录 `notify.py`。

| 渠道 | 需要的 Secrets | 说明 |
|---|---|---|
| Telegram | `TG_BOT_TOKEN` + `TG_USER_ID` | 可选 `TG_API_HOST`（自建反代）、`TG_PROXY`（HTTP 代理） |
| 飞书 | `FEISHU_WEBHOOK` | 群机器人 Webhook 完整地址或纯 key 均可 |
| 钉钉 | `DINGTALK_WEBHOOK` | 完整地址或纯 access_token；安全设置选「加签」时另配 `DINGTALK_SECRET` |
| 企业微信机器人 | `WECOM_WEBHOOK` | 兼容旧变量 `PUSH_WEBHOOK_URL`（两者都配时 `WECOM_WEBHOOK` 优先） |
| 企业微信应用 | `WECOM_CORPID` + `WECOM_CORPSECRET` + `WECOM_AGENTID` | 可选 `WECOM_TOUSER`（默认 @all） |
| Bark | `BARK_URL` | iOS 推送，完整地址（含设备 Key）或纯 Key 均可 |
| Server酱 | `SERVERCHAN_KEY` | 自动识别 Turbo 与 Server酱³ 的 Key |
| PushPlus | `PUSHPLUS_TOKEN` | 可选 `PUSHPLUS_TOPIC`（群组推送） |
| ntfy | `NTFY_TOPIC` | 可选 `NTFY_URL`（自建实例）、`NTFY_PRIORITY` |
| Gotify | `GOTIFY_URL` + `GOTIFY_TOKEN` | 可选 `GOTIFY_PRIORITY` |

原面板仓库的 `RAILGUN_PUSH_WEBHOOK_URL`（Railgun 单独推送）已弃用——通知统一走上表渠道，该 secret 可直接删除。

## 开启 / 关闭某个服务

1. **单个服务**：删除或清空对应 secret → 脚本自动跳过（推荐）；
2. **整个 workflow**：Actions 页面 Disable，或 `gh workflow disable panels.yml`；
3. **面板登录设备不一致时**：给对应面板配置 vars `GLADOS_USER_AGENT` / `RAILGUN_USER_AGENT` 覆盖默认 UA。
   注意：GLaDOS 系面板会校验签到请求客户端与登录设备平台一致，UA 与登录设备不一致会被反自动化检测拦截、积分被拒，默认固定为 Windows Chrome UA。

## 其他说明

- 本仓库由原 `glados-auto-checkin`、`railgun`、`NodeSeek-DeepFlood-LinuxSB-Checkin` 三个仓库合并而来；NodeSeek 部分保留了完整 git 历史，位于 `nodeseek/`。
- 通知模块统一位于根目录 `notify.py`，本地调试用的环境变量样例见 [`nodeseek/.env.example`](nodeseek/.env.example)。
- NodeSeek / DeepFlood / linux.sb 的详细说明与本地调试方法见 [`nodeseek/README.md`](nodeseek/README.md)。
