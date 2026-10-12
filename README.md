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

## Secrets 清单

| 名称 | 用途 |
|---|---|
| `GLADOS_ACCOUNTS_JSON` | GLaDOS 账号 JSON 数组（email + cookie） |
| `RAILGUN_ACCOUNTS_JSON` | Railgun 账号 JSON 数组（email + cookie） |
| `PUSH_WEBHOOK_URL` | 默认推送渠道（企业微信群机器人） |
| `RAILGUN_PUSH_WEBHOOK_URL` | Railgun 单独推送渠道（可选，留空则用默认） |
| `NS_COOKIE` / `DEEPFLOOD_COOKIE` / `LINUXSB_COOKIE` / `LINUXSB_ACCOUNT` / `NS_RANDOM` / `WECOM_WEBHOOK` | 三站签到与通知，详见 [`nodeseek/README.md`](nodeseek/README.md) |

## 开启 / 关闭某个服务

1. **单个服务**：删除或清空对应 secret → 脚本自动跳过（推荐）；
2. **整个 workflow**：Actions 页面 Disable，或 `gh workflow disable panels.yml`；
3. **面板登录设备不一致时**：给对应面板配置 vars `GLADOS_USER_AGENT` / `RAILGUN_USER_AGENT` 覆盖默认 UA。
   注意：GLaDOS 系面板会校验签到请求客户端与登录设备平台一致，UA 与登录设备不一致会被反自动化检测拦截、积分被拒，默认固定为 Windows Chrome UA。

## 其他说明

- 本仓库由原 `glados-auto-checkin`、`railgun`、`NodeSeek-DeepFlood-LinuxSB-Checkin` 三个仓库合并而来；NodeSeek 部分保留了完整 git 历史，位于 `nodeseek/`。
- NodeSeek / DeepFlood / linux.sb 的详细说明与本地调试方法见 [`nodeseek/README.md`](nodeseek/README.md)。
