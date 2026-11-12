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

GLaDOS / Railgun 的完整配置步骤、账号 JSON 示例、Cookie 获取及排障方法见
[`panels/README.md`](panels/README.md)。

面板脚本兼容原有 `email` + `cookie` 配置，可选增加账号级 `user_agent` / `impersonate`，无需批量重配。每个账号独立 HTTP Session，同账号复用连接和内存 Cookie 更新，但不提供独立浏览器/IP、不写回 Secrets。临时网络错误有限重试；任一账号或通知失败使工作流标红。手动运行勾选 `diagnose_only` 可只读查询登录状态，不签到、不推送。

面板工作流使用 `panels/requirements.txt` 固定关键依赖版本，并在仓库内跨分支互斥执行。日志区分首次奖励、今日观察记录和重复签到，GLaDOS 首次奖励不超过 2 分时提醒但不标记失败、不补签；Variable `GLADOS_LOW_REWARD_THRESHOLD` 可调整阈值，设为 `0` 关闭提醒。

可选 Secret `GLADOS_PROXY_URL` 仅用于 GLaDOS 的 challenge、签到及后续查询，所有请求使用同一显式代理；留空保持原网络行为，不影响 Railgun、其他站点或通知。它可连接已存在的 HTTP/SOCKS5 代理，包括已配置的 WARP 代理，但**不会安装或启用 WARP，也不保证提高积分**。代理必须可从实际运行器访问，仅在本机运行的代理不能直接供 GitHub 托管运行器使用；配置和排障见 [`panels/README.md`](panels/README.md)。

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

### 按站点单独配置（可选）

上表是所有站点共用的默认渠道。如果想让某个站点推送到不同地址，给变量名加上站点前缀即可：

| 站点 | 变量前缀 | 例（企微机器人地址） |
|---|---|---|
| GLaDOS | `GLADOS_` | `GLADOS_WECOM_WEBHOOK` |
| Railgun | `RAILGUN_` | `RAILGUN_WECOM_WEBHOOK` |
| NodeSeek / DeepFlood（同一条汇总消息） | `NODESEEK_` | `NODESEEK_WECOM_WEBHOOK` |
| linux.sb | `LINUXSB_` | `LINUXSB_WECOM_WEBHOOK` |

规则：**站点级变量优先于全局变量，且只覆盖配置了的那部分**——例如配了 `GLADOS_WECOM_WEBHOOK` 后，GLaDOS 的企微推送走新地址，而 GLaDOS 的 Telegram 等未配站点级的渠道仍走全局配置。所有渠道的所有变量都支持前缀（如 `GLADOS_TG_BOT_TOKEN` + `GLADOS_TG_USER_ID`、`LINUXSB_BARK_URL`）。

原面板仓库的旧变量 `RAILGUN_PUSH_WEBHOOK_URL` 已删除——Railgun 的企微地址现由站点级变量 `RAILGUN_WECOM_WEBHOOK` 承担（默认复用 `PUSH_WEBHOOK_URL` 的值，与 GLaDOS 同群）。

## 开启 / 关闭某个服务

1. **单个服务**：删除或清空对应 secret → 脚本自动跳过（推荐）；
2. **整个 workflow**：Actions 页面 Disable，或 `gh workflow disable panels.yml`；
3. **面板登录设备不一致时**：给对应面板配置 vars `GLADOS_USER_AGENT` / `RAILGUN_USER_AGENT`（未设置时回退同名 Secret），或给账号 JSON 增加可选 `user_agent` 字段覆盖默认 Windows Chrome 124 UA。账号级设置优先，只规范化复制时附带的请求头前缀、外层引号和两端空格，不改写浏览器平台或版本；UA 和近似传输预设不能保证完整浏览器指纹一致。

## 其他说明

- 本仓库由原 `glados-auto-checkin`、`railgun`、`NodeSeek-DeepFlood-LinuxSB-Checkin` 三个仓库合并而来；NodeSeek 部分保留了完整 git 历史，位于 `nodeseek/`。
- 通知模块统一位于根目录 `notify.py`，本地调试用的环境变量样例见 [`nodeseek/.env.example`](nodeseek/.env.example)。
- GLaDOS / Railgun 的详细配置见 [`panels/README.md`](panels/README.md)；面板脚本直接读取环境变量，不自动加载 `.env`。
- NodeSeek / DeepFlood / linux.sb 的详细说明与本地调试方法见 [`nodeseek/README.md`](nodeseek/README.md)。
