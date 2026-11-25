# NodeSeek 自动签到评论加鸡腿脚本

这是一个用于 NodeSeek 及同站体系（DeepFlood）、并附带 linux.sb（烧饼社区）站的每日自动签到脚本。三站均使用 Selenium 和 undetected-chromedriver 应对 Cloudflare 防护；linux.sb 额外保留一条 requests 快速通道（`linuxsb_daily.py`），站点未开挑战时直接用它签到。三站可顺序执行、一站失败不影响其他站。

强烈建议修改随机词。否则容易被举报被禁言。有能力的可以fork后自己定义改。

## 功能特点

- NodeSeek / DeepFlood / linux.sb 多站自动签到
- 自动点击"试试手气"或"鸡腿 x 5"按钮（可配置）
- 随机选择帖子进行评论
- 自动给帖子加鸡腿（7天内的帖子）
- 随机评论内容（"bd"、"绑定"、"帮顶"）
- 除签到外的任务有一键开关，默认关闭
- 支持 GitHub Actions 自动运行
- 支持无头模式（可配置）
- 任务结束后推送通知（Telegram、飞书、钉钉、企业微信、Bark、Server酱、PushPlus、ntfy、Gotify 等渠道，见仓库根目录 README）

## 环境变量配置

### 基础配置

- `NS_COOKIE`: NodeSeek 的 Cookie（必需）。建议**整段复制**（含 HttpOnly 的 `session`）：`session` 只在浏览器发出的请求头里可见，用控制台 `document.cookie` 之类的方式复制会漏掉它，浏览器就会一直停在未登录状态。正确做法：浏览器登录后按 F12 → 网络 → 刷新页面 → 点该站请求 → 请求头 → 复制整条 `Cookie` 的值。粘贴时带了 `Cookie:` 前缀、外层引号、curl 的 `-H`/`--cookie`、BOM 或零宽字符等包裹也没关系，脚本会自动清洗掉
- `NS_RANDOM`: 是否随机选择奖励，true/false（可选，默认 false）
- `HEADLESS`: 是否使用无头模式，true/false（可选，默认 true）。**注意 GitHub Actions 中需用有头模式（`false`）配合 xvfb 才能通过 Cloudflare 挑战**，workflow 已硬编码为 `false`，本地无显示环境时可用 `true`
- `NS_EXTRA_TASKS`: 除签到外的任务（评论、加鸡腿）总开关，true/false（可选，**默认 false**）
- `DEEPFLOOD_COOKIE`: DeepFlood 子站的 Cookie（可选）。配置后会自动追加签到第二站；两站用同一套代码、同样页面结构，仅域名与 cookie 不同。同样建议整段复制（含 `session`）
- `NS_WARP`: Actions 仓库 Variable，默认启用 WARP。设为 `false` 或 `0` 可恢复直连；无需配置 WARP 密钥或在本机安装 VPN
- `NS_PROXY`: 可选的浏览器代理，例如 `socks5://127.0.0.1:40000`。Actions 中作为 Secret 配置后会替代内置 WARP；支持 HTTP、HTTPS、SOCKS5，暂不支持用户名密码认证。只影响 NodeSeek / DeepFlood，不影响 linux.sb、GLaDOS 或通知
- `NS_VERIFY_ONLY`: 设为 `1` 时仅验证网络与登录态，不领取奖励、不评论、不加鸡腿、不推送。Actions 中可直接勾选 `verify_only`，同时跳过 linux.sb
- `LINUXSB_COOKIE`: linux.sb（烧饼社区）的 Cookie（可选）。配置后会在 NodeSeek / DeepFlood 之后追加签到一站（`linuxsb_daily.py`）。该站 2026-08 起会**间歇性**开启 Cloudflare 托管挑战（同一出口 IP 可能上一轮 403、下一轮 200），脚本会先用 requests 探测：能直连就走 requests 快通道，被挑战（HTTP 403 + `Cf-Mitigated: challenge`）则自动把 Cookie 注入浏览器过盾后签到。多账号用 `&` 分隔（`cookie1&cookie2`），依次签到、单账号失败不中断
- `LINUXSB_ACCOUNT`: linux.sb 的账号密码兜底登录（可选），格式为 JSON：`{"username":"你的用户名","password":"你的密码"}`。**Cookie 优先**：`LINUXSB_COOKIE` 有效时完全不用凭据；Cookie 缺失或失效时自动用浏览器登录（算术题验证码由脚本解出填写，PoW 由页面 JS 计算），登录成功当场在同一浏览器会话内继续签到，无需手动换 cookie
- `LINUXSB_FORCE_BROWSER`: 置 `1` 时 linux.sb 跳过 requests 探测直接走浏览器通道（可选）。用于站点长期开盾时省掉必然失败的探测，或在挑战未触发的时段验证浏览器通道
- `LINUXSB_PROXY`: linux.sb 浏览器的显式 HTTP/HTTPS/SOCKS5 代理（可选，不支持认证）。配置后跳过 requests 直连通道；不影响其他站点或通知。Actions 默认复用内置 WARP，也可用同名 Secret 单独覆盖。
- `LINUXSB_WARP_REQUIRED`: 置 `1` 时，浏览器必须通过 `warp=on/plus` 检查才访问账号；验证失败停止，不静默改为直连。内置 WARP 模式由 workflow 自动设置。
- `SITE_GAP_MIN` / `SITE_GAP_MAX`: 各站签到之间的随机延迟范围（秒，可选，默认 60-180），降低连续签到被风控的概率

布尔类型变量接受 `true`/`1`/`yes`/`on`/`y`（大小写不敏感）为真，其余值一律为假。

### WARP 与 IP 风控

Actions 默认使用项目内的用户态 WARP SOCKS5 代理（[wgcf v2.3.1](https://github.com/ViRb3/wgcf/releases/tag/v2.3.1) + [wireproxy v1.1.3](https://github.com/windtf/wireproxy/releases/tag/v1.1.3)），下载文件校验固定 SHA-256，不使用公开共享私钥、不改系统路由。NodeSeek / DeepFlood / linux.sb 浏览器均可复用内置代理，linux.sb 完成后才关闭代理。每次 runner 生成的账号和配置只保留在忽略目录 `.cache/nodeseek/warp/`，权限为私有，不上传日志或缓存凭据。

日志会分别验证 `WARP SOCKS5 proxy verified` 和各浏览器的 `浏览器 WARP 验证通过 (warp=on/plus)`，后者用于防止“curl 已换出口，浏览器却仍然直连”。内置 WARP 建连或浏览器验证失败会报错停止对应站点签到，不会静默回退到原 IP。配置自备 `NS_PROXY` 时不启动内置 WARP，linux.sb 默认保持原通道，或通过独立 `LINUXSB_PROXY` 指定代理。最终是否签到成功，以各站的实际领取结果为准。

Cloudflare 挑战超时不等于账号封禁，也不能单凭它认定 Cookie 过期。脚本区分挑战超时、明确的 IP/WAF 拦截和登录失效，不再反复在同一个挑战页尝试 Cookie 注入。WARP 可用于替换 Actions 出口，但不能保证绕过所有风控，也不能修复失效 Cookie 或账号限制。

需要关闭内置 WARP 时，在 Settings -> Secrets and variables -> Actions -> Variables 添加 `NS_WARP=false` 即可，无需重新获取 Cookie。自备代理则配置 Secret `NS_PROXY`。

### 关于多站点签到

DeepFlood 是 NodeSeek 的子站，同一套论坛代码、同样的页面结构，只是独立域名与独立登录态。linux.sb（烧饼社区）则是另一套论坛程序（bbs1），同样挂在 Cloudflare 后面。各站配置情况：

- 配置 `DEEPFLOOD_COOKIE`：NodeSeek 与 DeepFlood 共用同一个浏览器实例，各自注入自己的 cookie 后签到，互不干扰
- 配置 `LINUXSB_COOKIE`：NodeSeek / DeepFlood 完成后，workflow 追加执行 `linuxsb_daily.py` 完成第三站签到。requests 通道被 Cloudflare 挑战时自动改用浏览器（因此该步骤同样以 `xvfb-run` + `HEADLESS=false` 运行）
- 无论配置几站，站与站之间都会随机等待 `SITE_GAP_MIN`~`SITE_GAP_MAX` 秒，避免两次签到紧挨着被判定为机器批量行为
- 通知按站点分段显示，各自带自己的签到结果

### 关于 NS_EXTRA_TASKS

签到是无风险操作，始终执行。评论和加鸡腿会在他人帖子下留言，有被举报禁言的风险，因此单独用 `NS_EXTRA_TASKS` 控制，且默认关闭：

- 不配置或设为 `false`：只签到，不评论、不加鸡腿
- 设为 `true`：签到 + 评论 + 加鸡腿（建议先修改 `randomInputStr` 里的随机词）

### 通知配置（全部可选）

每次任务执行完毕后会汇总签到结果、评论数量、加鸡腿状态并推送。通知由仓库根目录 `notify.py` 统一实现，支持的全部渠道与对应环境变量见仓库根目录 [README 的「通知渠道」章节](../README.md#通知渠道全部可选)：只配置哪个渠道就推送到哪个渠道，未配置的自动跳过，某个渠道失败不影响其他渠道，也不会导致任务失败。

本模块的两个站点支持站点级覆盖：变量名前加 `NODESEEK_` / `LINUXSB_` 前缀即可让该站推送到单独地址（如 `NODESEEK_TG_BOT_TOKEN`、`LINUXSB_BARK_URL`），只覆盖配置了的那部分，其余仍回退全局渠道变量。

LinuxSB 的积分显示保留网页中的小数和 `K` / `M` 单位（如 `5K`、`5.25K`），仍兼容 `1,234` 等原格式。缩写积分不擅自换算成精确整数，签到奖励与累计积分仍分开展示。

LinuxSB 的“滑动验证”页面不是账号概览，不能把它当成模板差异或 Cookie 过期。访问时仍被验证拦截会明确报告；签到成功后的刷新若被拦截，只报告概览获取失败，不重新提交签到。

维护时可运行 `python -B nodeseek/linuxsb_daily.py --overview-only`，只使用现有 Cookie 读取当前积分，不签到、不请求账号密码登录、不推送通知。推送含 `[linuxsb-verify]` 的提交时，`nodeseek-tests` 会在离线回归后追加同一运行器的直连/WARP 只读对照；普通代码推送仍仅运行离线测试，不额外签到。

通知内容示例（多站签到，实际推送格式）：

```
NodeSeek 每日任务

任务开始时间: 2026-07-30 08:00:12
【NodeSeek】
签到时间: 2026-07-30 08:00:15
签到结果: 签到成功，今天的签到收益是6个鸡腿 OK
当前等级: Lv 1
总鸡腿数: 124
评论数: 4
附加任务: 已关闭（NS_EXTRA_TASKS 未开启）

【DeepFlood】
签到时间: 2026-07-30 08:02:08
签到结果: 签到成功，今天的签到收益是5个鸡腿 OK
当前等级: Lv 0
总鸡腿数: 98
评论数: 0
附加任务: 已关闭（NS_EXTRA_TASKS 未开启）
```

顶部"任务开始时间"为脚本启动时刻，各站段首的"签到时间"为该站开始签到时刻，两者的差值即两站间的随机延迟。

只配置单站时，通知只有【NodeSeek】一段，格式相同。

签到前会先抓一次账号概览来确认登录态：只有确实登录（抓到等级/鸡腿等账号信息）时，"页面显示已签到"才会被判为成功。**判定只看页面证据，不依赖任何 cookie 名**——日志里出现的"cookie 串里没有 session 字段"只是排查线索，不是判据。抓不到概览时通知里会出现 `账号概览: 未抓到（未登录或页面结构已变化）`，此时签到结果一律如实报失败，不会静默报成功。

若 cookie 串开头有一段无法归属的无名片段（粘贴被从头截断的典型形态）且整串没有 `session`，脚本会把该片段按"被截掉名字的登录凭据"试注入一次：随后的账号概览能抓到账号信息，就说明它正是 `session`（本次无需重新粘贴）；抓不到则不采信，仍如实报失败并在日志里点明粘贴确实被截断。

## 本地运行

1. 克隆仓库
2. 安装依赖：`pip install -r requirements.txt`
3. 设置环境变量（可使用 .env 文件）
4. 运行脚本：`python nodeseek_daily.py`

## GitHub Actions 自动运行

1. Fork 本仓库
2. 在仓库的 Settings -> Secrets and variables -> Actions 中添加 Secret `NS_COOKIE`
3. 可选：添加 `NS_RANDOM` 设置是否随机选择奖励
4. 可选：需要评论和加鸡腿时，添加 `NS_EXTRA_TASKS=true`（不配置则只签到）
5. 可选：配置多站签到 `DEEPFLOOD_COOKIE` / `LINUXSB_COOKIE`（不配置则只签 NodeSeek 一站；linux.sb 多账号用 `&` 分隔；linux.sb 可另配 `LINUXSB_ACCOUNT`（JSON 账号密码）作 cookie 失效时的自动登录兜底）
6. 可选：添加通知渠道的 Secrets（渠道与环境变量清单见仓库根目录 README「通知渠道」），workflow 已预置全部通知变量，未添加的自动跳过
7. Actions 会在每天 UTC 00:00（北京时间 08:00）自动运行，也可在 Actions 页面手动触发（workflow_dispatch）

手动运行时勾选 `nodeseek_only`，可只验证 NodeSeek / DeepFlood，自动关闭评论和加鸡腿，不运行 linux.sb。维护时也可修改 `.github/nodeseek-checkin.trigger` 并以包含 `[nodeseek-checkin]` 的提交推送到 `main`，触发同样的两站专用签到验证；其他代码提交不会额外触发正式签到。

已经签到过、只想确认出口和登录态时，勾选 `verify_only`；通过推送触发时，提交信息同时包含 `[nodeseek-checkin]` 和 `[nodeseek-verify]` 即使用同样的只读模式。

`NS_EXTRA_TASKS` 和 `NS_RANDOM` 这类非敏感开关既可以放在 Variables 也可以放在 Secrets，workflow 会优先取 Variables。放在 Variables 的好处是能在页面上直接看到当前值。

## 注意事项

- 请确保 Cookie 有效且具有足够的权限
- 评论内容较为简单，开启 `NS_EXTRA_TASKS` 前建议先修改 `randomInputStr` 列表
- 加鸡腿功能仅对 7 天内的帖子有效
- 通知为可选功能，未配置任何渠道时脚本行为与之前完全一致
- 某个渠道推送失败只打印日志，不会影响签到结果和其他渠道
- GitHub Actions 中 Telegram 可能受网络限制，必要时用 `TG_API_HOST` 指向自建反代
- 定时任务的实际触发时间受 GitHub Actions 队列影响，通常会比 08:00 晚几分钟到几十分钟

## 测试

不启动浏览器即可运行全部单测：

```bash
python -m unittest discover -p "test_*.py" -v
```
