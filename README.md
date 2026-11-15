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

- `panels.yml` —— GLaDOS（默认原生 Chrome + Playwright/CDP）+ Railgun（HTTP API）
- `daily.yml` —— NodeSeek + DeepFlood + linux.sb（浏览器过盾）

GLaDOS / Railgun 的完整配置步骤、账号 JSON 示例、Cookie 获取及排障方法见
[`panels/README.md`](panels/README.md)。

面板脚本仍接受原有 `email` + `cookie` 配置。GLaDOS 默认通过原生 Chrome 访问官网签到页，使用浏览器实际 UA、独立账号上下文和官网验证流程；不提供每账号独立 IP，也不保证旧 Cookie 通过设备校验。Variable `GLADOS_BROWSER_CHECKIN=0` 可切回旧 HTTP 方式，Railgun 和其他站点保持原流程。浏览器已提交但结果未知时不重复签到；任一账号或通知失败仍使工作流标红。

可选配置 `GLADOS_GMAIL_ADDRESS`、`GLADOS_GMAIL_APP_PASSWORD`、`GLADOS_COOKIE_STORE_KEY` 三个 Secrets，并将账号验证码邮件转发到该 Gmail，可在明确登录失效或设备不一致时有限恢复会话。恢复后的 Cookie 只保存为项目内加密文件，通过 Actions cache 复用，不写回 Secrets，不缓存浏览器明文配置。未配置邮箱时仍可使用有效 Cookie。

手动运行勾选 `diagnose_only` 仅查询账号状态；勾选 `verify_only` 仅检查 GLaDOS 浏览器挑战和验证是否就绪，不签到、不发验证码、不保存会话、不推送。两项不能同时选择。浏览器验证就绪不等于真实签到已经成功。

面板工作流使用 `panels/requirements.txt` 固定关键依赖版本，并在仓库内跨分支互斥执行。日志区分首次奖励、今日观察记录和重复签到，GLaDOS 首次奖励不超过 2 分时提醒但不标记失败、不补签；Variable `GLADOS_LOW_REWARD_THRESHOLD` 可调整阈值，设为 `0` 关闭提醒。

可选 Secret `GLADOS_PROXY_URL` 用于 GLaDOS 浏览器、登录 API 和后续查询；所有账号共用同一显式代理，故障不静默直连。浏览器模式留空时直连，不继承环境代理；旧 HTTP 方式保持原环境代理规则。不影响 Railgun、其他站点、Gmail IMAP 或通知。支持现有 HTTP/HTTPS 和无认证 SOCKS5 代理，包括已配置的 WARP 代理，但**不会安装或启用 WARP，也不保证提高积分**。代理必须可从实际运行器访问；配置和排障见 [`panels/README.md`](panels/README.md)。

## Secrets 清单（账号类）

| 名称 | 用途 |
|---|---|
| `GLADOS_ACCOUNTS_JSON` | GLaDOS 账号 JSON 数组（email + cookie） |
| `GLADOS_GMAIL_ADDRESS` | 可选：统一接收验证码邮件的 Gmail，三项须一起配置 |
| `GLADOS_GMAIL_APP_PASSWORD` | 可选：上述 Gmail 的应用专用密码，不是登录密码 |
| `GLADOS_COOKIE_STORE_KEY` | 可选：固定 Fernet 加密密钥，用于跨次运行复用会话 |
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
3. **登录设备不一致时**：GLaDOS 浏览器模式使用浏览器实际 UA，可更新有效 Cookie 或配置邮箱恢复；旧 HTTP 方式和 Railgun 才使用 `GLADOS_USER_AGENT` / `RAILGUN_USER_AGENT` 或账号级 `user_agent`。UA 和近似传输预设不能保证完整浏览器指纹一致。

## 其他说明

- 本仓库由原 `glados-auto-checkin`、`railgun`、`NodeSeek-DeepFlood-LinuxSB-Checkin` 三个仓库合并而来；NodeSeek 部分保留了完整 git 历史，位于 `nodeseek/`。
- 通知模块统一位于根目录 `notify.py`，本地调试用的环境变量样例见 [`nodeseek/.env.example`](nodeseek/.env.example)。
- GLaDOS / Railgun 的详细配置见 [`panels/README.md`](panels/README.md)；面板脚本直接读取环境变量，不自动加载 `.env`。
- NodeSeek / DeepFlood / linux.sb 的详细说明与本地调试方法见 [`nodeseek/README.md`](nodeseek/README.md)。
