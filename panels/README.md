# GLaDOS / Railgun 配置指南

两个站点共用 `checkin.py` 和 GitHub Actions 工作流 `panels.yml`，账号配置独立，未配置的站点自动跳过。通知渠道由仓库根目录的 `notify.py` 统一处理。

## 1. 配置入口

在自己的仓库打开 **Settings → Secrets and variables → Actions**。

| 站点 | 当前接口域名 | Secrets：账号配置 | Variables：浏览器 UA（可选） |
|---|---|---|---|
| GLaDOS | `https://glados.cloud`（支持备用域名，见下文） | `GLADOS_ACCOUNTS_JSON` | `GLADOS_USER_AGENT` |
| Railgun | `https://railgun.info` | `RAILGUN_ACCOUNTS_JSON` | `RAILGUN_USER_AGENT` |

- 账号配置和推送密钥放在 **Secrets → New repository secret**，不要放进公开 Variables、代码或 Issue。
- 旧 HTTP 方式的 User-Agent 可放在 **Variables → New repository variable**，或同名 Secret；工作流优先读取非空 Variable，未设置时回退同名 Secret。账号级 `user_agent` 仍优先于面板配置。GLaDOS 浏览器方式使用原生浏览器实际 UA，不使用这些 UA 覆盖。
- 只使用一个站点时，仅配置该站点的账号 Secret，另一个留空。
- Fork 用户先同步仓库代码，再填写自己的配置；同步代码不会同步或更新 Secrets。

## 2. 获取 Cookie 和 User-Agent

1. 用平时使用的浏览器打开对应站点并登录，确认控制台可以查看账号信息。
2. 按 F12 打开开发者工具，切换到 **Network / 网络**，刷新控制台页面。
3. 找到对应站点的 `/api/user/status` 请求。先查看响应 JSON，确认有账号 `data`；仅有 HTTP 200 不代表登录成功。
4. 在 **Request Headers / 请求标头** 中复制 `Cookie` 的完整值，包括所有字段名、等号、值和分号。脚本也兼容带 `Cookie:` 前缀的整行。
5. 若使用 Railgun 或 GLaDOS 旧 HTTP 方式，从同一条请求复制完整的 `User-Agent`，填入该站点的 UA Variable，或对应账号的可选 `user_agent` 字段。GLaDOS 浏览器模式无需手填 UA，但不同设备登录的旧 Cookie 仍可能被拒绝。

GLaDOS 当前常见的签名会话结构如下，值只是占位符：

```text
gld:sess=REPLACE_WITH_SESSION; gld:sess.sig=REPLACE_WITH_SIGNATURE
```

两项必须来自同一份浏览器会话，不能混用不同账号或不同登录时间的值。Railgun 以它自己当前请求中的 Cookie 字段为准，不要直接套用 GLaDOS 的 Cookie。

不要只复制某一项的值，也不要复制响应的 `Set-Cookie` 标头；其中的 `Path`、`Expires`、`HttpOnly` 等属性不是请求 Cookie 的账号字段。Cookie-Editor 导出的 `[{"name":"gld:sess","value":"..."}]` 式结构也不能直接填入 `cookie`：当前脚本需要的是请求头字符串，而不是 Cookie 导出数组或对象。

## 3. 账号 JSON 示例

以下全部是虚构占位值，使用前替换为各自账号的真实请求 Cookie。

### GLaDOS 单账号

把整个 JSON 数组填入 Secret `GLADOS_ACCOUNTS_JSON`：

```json
[
  {
    "email": "glados-account@example.com",
    "cookie": "gld:sess=REPLACE_WITH_SESSION; gld:sess.sig=REPLACE_WITH_SIGNATURE"
  }
]
```

### GLaDOS 多账号

每个账号对应一个数组元素，不使用 `&`、换行或逗号拼接多个 Cookie 字符串：

```json
[
  {
    "email": "first-account@example.com",
    "cookie": "gld:sess=FIRST_SESSION; gld:sess.sig=FIRST_SIGNATURE"
  },
  {
    "email": "second-account@example.com",
    "cookie": "gld:sess=SECOND_SESSION; gld:sess.sig=SECOND_SIGNATURE"
  }
]
```

### Railgun

把整个 JSON 数组填入 Secret `RAILGUN_ACCOUNTS_JSON`，将 `COPY_FULL_RAILGUN_COOKIE_HEADER` 替换成 Railgun 当前请求的完整 Cookie 值。多账号同样增加数组元素：

```json
[
  {
    "email": "railgun-account@example.com",
    "cookie": "COPY_FULL_RAILGUN_COOKIE_HEADER"
  }
]
```

未启用邮箱恢复时，`email` 可作为通知显示标识；浏览器模式中若填写了有效邮箱，还会核对状态接口返回的账号邮箱，防止用错会话。启用邮箱恢复时，必须填写该 GLaDOS 账号的真实登录邮箱，它参与登录和缓存绑定。`cookie` 必须是字符串；未启用恢复时不能留空，启用恢复后可填 `""` 以在首次运行登录。

推荐直接粘贴标准 JSON 数组，不加 Markdown 围栏、不额外包一层引号。JSON 字符串内的双引号和反斜杠需要转义；字符串值内不要插入实际换行。外层数组可以正常缩进换行。脚本虽兼容双重 JSON 编码，首次配置无需使用它。

## 4. 浏览器、User-Agent 和通知

### GLaDOS 原生浏览器方式

`panels.yml` 默认启用 `GLADOS_BROWSER_CHECKIN=1`。使用运行器已安装的 Google Chrome，通过 Playwright/CDP 连接，Linux 有头模式在 Xvfb 下运行；不默认下载另一套浏览器。每个账号使用独立的浏览器上下文，同一轮仍共用 Chrome 进程和出口网络。Railgun、NodeSeek、DeepFlood、linux.sb 和通知实现不变。

这次改造参考 [Ingress007/GLaDOS-Auto-Checkin 的固定版本](https://github.com/Ingress007/GLaDOS-Auto-Checkin/tree/bdb7ce6cb41f6d74f7e5f503fb947cc3e411f967) 的原生 Chrome、官方验证和邮箱会话恢复思路，使用本项目自己的实现，未整文件复制其代码。参考仓库[2026-11-15 的运行](https://github.com/Ingress007/GLaDOS-Auto-Checkin/actions/runs/43613098333)有首次奖励，但不代表本仓库账号已经恢复，也不保证未来持续通过验证。

浏览器先核对登录状态，再打开 `/console/checkin`，等待官网 challenge 和 Turnstile。仅在官网产生有效 nonce、proof 和所需验证参数后允许签到 POST；每个上下文最多提交一次，其他应用写请求被拦截。验证未就绪或挑战过期时不提交；已经提交但响应丢失、断线或格式未知时不切域名、不重新登录、不重放 POST。明确认证/设备拒绝时，可按下一节有限恢复一次。

实际浏览器 UA 同时用于浏览器和辅助 HTTP 查询/邮箱登录，不随机轮换，也不接受账号或面板 `user_agent` 覆盖。账号 `impersonate` 仅影响辅助 `curl_cffi` 请求，不能改变原生浏览器指纹。复制自其他设备的 Cookie 可能仍然 `device-mismatch`；更新 Cookie 或配置邮箱恢复，而不是伪造另一台设备的 UA。

| 配置 | 默认值 | 说明 |
|---|---|---|
| Variable `GLADOS_BROWSER_CHECKIN` | 工作流 `1`；本地未设置时 `0` | `1` / `true` 启用，`0` / `false` 切回旧 HTTP 方式。 |
| Variable `GLADOS_BROWSER_TIMEOUT_SECONDS` | `120` | 验证/请求等待时间，整数 `10` 到 `300` 秒。 |
| 本地环境 `GLADOS_BROWSER_HEADLESS` | `false` | 工作流使用 Xvfb 有头模式；本地可设 `true`，但不保证风控效果相同。 |
| 本地环境 `GLADOS_BROWSER_CHANNEL` | `chrome` | 可选 `chrome`、`msedge`、`chromium`；后者需自行安装到项目内浏览器目录。 |
| 本地环境 `GLADOS_BROWSER_EXECUTABLE` | 自动查找 | 可指定已安装的 Chrome/Edge 可执行文件，只作为程序输入。 |
| 本地环境 `GLADOS_RUNTIME_DIR` | `.cache/panels` | 必须解析到本项目内，项目外路径或指向项目外的链接会被拒绝。 |

### 可选 Gmail 恢复和加密缓存

沿用原 `GLADOS_ACCOUNTS_JSON`，额外配置三个 **Secrets**，必须一起填写：

| Secret | 用途 |
|---|---|
| `GLADOS_GMAIL_ADDRESS` | 接收各账号验证码的统一 Gmail 邮箱。 |
| `GLADOS_GMAIL_APP_PASSWORD` | 开启两步验证后生成的 Gmail 应用专用密码，用于 IMAP；不要填写主登录密码。 |
| `GLADOS_COOKIE_STORE_KEY` | 固定 Fernet 密钥，用于加密会话缓存；保持不变才能复用旧缓存。 |

可以使用已安装的 `cryptography` 在本地生成 Fernet 密钥，将结果直接存入 Secret，不提交代码或粘贴到 Issue：

```shell
python -B -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode('ascii'))"
```

将各 GLaDOS 登录邮箱收到的验证码邮件自动转发到该 Gmail，保留原收件人 `To`、`X-Original-To` 或 `X-Gm-Original-To`。脚本核对目标账号、发送者、邮件时间和新 UID，拒绝旧验证码、其他账号或多个不同验证码，不猜测。默认允许发送者域名为 `glados.network`、`glados.cloud`、`glados.one`；如官方发送地址改变，可通过 Variable `GLADOS_GMAIL_SENDERS` 配置域名或完整地址，以逗号分隔。不要配置任意发送者。

Variable `GLADOS_GMAIL_WAIT_SECONDS` 默认 `120`，范围 `10` 到 `600` 秒。Gmail 通过 `imap.gmail.com:993` 直连，只读打开 INBOX，使用 `BODY.PEEK[]`，不删除或主动标记邮件已读；`GLADOS_PROXY_URL` 不代理 IMAP。

每轮先检查缓存/配置 Cookie 的账号身份。完整有效会话不发验证码；无会话或明确登录失效/设备拒绝时最多登录一次。本轮已登录后仍拒绝则停止。Turnstile 拒绝、网络错误、HTML、身份不符和签到提交后结果未知都不触发额外登录。登录 API 如果要求额外人工验证，脚本停止，不反复请求验证码。

会话按真实邮箱和来源域名绑定，原子写入 `.cache/panels/sessions.enc`，仅此加密文件通过 Actions cache 恢复/保存；密钥只来自 Secret。运行器缓存可能被淘汰，旧密钥无法解密时不会盲用缓存。浏览器明文临时配置在结束时清理，整个 `.cache/` 已忽略，不写回账号 Secret。换密钥后需重新建立有效会话。

未配置这三个 Secrets 时，仍可用有效 Cookie 签到，但不自动登录或跨次缓存会话；不应将此配置缺失误报为 Cookie 自然到期。

### 旧 HTTP 方式的 User-Agent

**以下 UA 规则仅适用于 Railgun 和 GLaDOS 旧 HTTP 方式。** 默认使用固定 Windows Chrome 124 UA，与默认 `chrome124` 传输预设一致。不自动改写已配置 UA 的浏览器平台或版本，只移除复制时附带的 `User-Agent:` 前缀、成对外层引号及两端空格。控制字符、无法发送的字符、空值包装或重复包装会在创建 Session 前拒绝，错误不输出原始值。

UA 优先级为：账号 `user_agent` > 面板 `GLADOS_USER_AGENT` / `RAILGUN_USER_AGENT` > 默认值。若登录浏览器在 macOS、Linux、手机或使用其他浏览器，请使用同一浏览器请求里的 UA。不同账号可以使用不同 UA，但不随机轮换。

账号可选字段如下，两个站点通用：

| 字段 | 默认行为 | 说明 |
|---|---|---|
| `user_agent` | 继承面板 UA 或默认 UA | 同账号登录请求的完整 UA 字符串。 |
| `impersonate` | 根据 UA 自动选择预设 | 可选的 `curl_cffi` 浏览器传输预设名，例如 `chrome124`；必须由当前安装版本支持，不支持时该账号失败且不发送请求。 |

可按需扩展现有账号，不必修改其他账号：

```json
[
  {
    "email": "first-account@example.com",
    "cookie": "gld:sess=FIRST_SESSION; gld:sess.sig=FIRST_SIGNATURE",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "impersonate": "chrome124"
  },
  {
    "email": "second-account@example.com",
    "cookie": "gld:sess=SECOND_SESSION; gld:sess.sig=SECOND_SIGNATURE"
  }
]
```

通常不需要指定 `impersonate`。自动选择同浏览器家族、相同或最接近版本的可用预设；没有合适预设时回退 `chrome124` 并提示。Safari、显式预设及缺少精确版本的情况按近似处理。Chromium 请求头提示会按 UA 构造，但 UA 与传输预设仍不等于完整真实浏览器环境，不能保证通过设备校验。

请求显式携带 `Accept-Language` 和 `Sec-Fetch-Dest` / `Sec-Fetch-Mode` / `Sec-Fetch-Site`。日志只展示 UA 来源（账号、面板或默认）、传输预设、是否规范化，以及使用默认网络还是显式代理，不输出完整 UA。

旧 HTTP 方式的每个账号拥有独立的 HTTP Session 和 CookieJar，同账号的签到、状态、积分和重试复用该 Session，处理结束后关闭。**HTTP Session 不是独立真实浏览器，也不提供不同出口 IP**；GitHub Actions 上的多个账号通常仍共用运行器网络。不要把随机 UA 或 Session 隔离视为能消除风控。

### 通知

通知配置都是可选的。完整渠道列表见[根目录 README](../README.md) 的“通知渠道”章节，常见配置如下：

| 渠道 | 全局 Secrets | 只覆盖 GLaDOS | 只覆盖 Railgun |
|---|---|---|---|
| 企业微信机器人 | `WECOM_WEBHOOK` | `GLADOS_WECOM_WEBHOOK` | `RAILGUN_WECOM_WEBHOOK` |
| 飞书机器人 | `FEISHU_WEBHOOK` | `GLADOS_FEISHU_WEBHOOK` | `RAILGUN_FEISHU_WEBHOOK` |
| Telegram | `TG_BOT_TOKEN` + `TG_USER_ID` | `GLADOS_TG_BOT_TOKEN` + `GLADOS_TG_USER_ID` | `RAILGUN_TG_BOT_TOKEN` + `RAILGUN_TG_USER_ID` |

没有站点级配置时，通常使用全局渠道；配置某站的一个渠道不会清空它的其他全局渠道。

**企业微信的历史配置例外：** 当前 `panels.yml` 会优先把站点级 `*_WECOM_WEBHOOK` 或旧 Secret `PUSH_WEBHOOK_URL` 注入面板的企微配置。若保留了 `PUSH_WEBHOOK_URL`，仅修改全局 `WECOM_WEBHOOK` 不一定改变这两个站点的推送地址。需要换群时，直接配置对应的 `GLADOS_WECOM_WEBHOOK` / `RAILGUN_WECOM_WEBHOOK`。旧变量 `RAILGUN_PUSH_WEBHOOK_URL` 已不再使用。

未配置任何通知渠道时，签到仍会执行，但不会推送。

## 5. 运行和验收

1. 打开 **Actions → panels-checkin → Run workflow**，选择自己仓库的主分支运行。首次排查可勾选 `diagnose_only` 检查状态，或勾选 `verify_only` 检查浏览器验证，不要同时选择。
2. 查看 `Run checkin` 步骤和通知中的实际结果，分别确认两个站点是否被跳过或执行。
3. 核对官网的签到记录、积分和剩余天数，不要只看工作流的绿色成功标记。

默认运行会实际发送签到请求，并向已配置渠道推送。勾选 `diagnose_only` 后只 GET `/api/user/status`，不签到、不查询积分、不推送、不修改 Secrets；只读诊断成功也不保证签到设备校验通过。

如 SSH 可以推送但工作流 API 认证不可用，可修改 `.github/panels-checkin.trigger`，用包含 `[panels-checkin]` 的提交说明推送到 `main`。只有这个明确标记的触发文件变更才执行正式签到；普通代码推送不会签到。该入口同样使用已有账号 Secrets 和并发限制，不是只读诊断。

`verify_only` 仅检查 GLaDOS：核对已有会话和邮箱、加载官方 challenge、等待 Turnstile 就绪并查询状态；不点击签到按钮、不发送应用签到/登录 POST、不访问邮箱、不保存会话缓存、不推送，并跳过 Railgun。官网验证自身的 Cloudflare 通信仍会发生。它需要已有有效 Cookie 或可解密缓存，验证就绪也不是签到入账证明。与 `diagnose_only` 同时启用会直接失败，不执行账号任务。

任一账号的签到或查询失败、配置异常或已配置通知渠道失败都会使脚本返回非零状态，工作流标红；其他账号和站点仍继续处理。未配置站点或空数组不算失败。日志只展示账号序号和安全错误分类，不输出邮箱、Cookie、原始服务端错误文本或通知密钥；通知仍使用配置中的账号标识。

定时为 UTC 的 `0 */8 * * *`，对应北京时间约 00:00、08:00、16:00；GitHub 调度可能延迟，并非保证精确到点。本仓库使用同一 `panels-checkin` 并发组，跨分支的定时、手动和诊断运行也互斥，不取消正在运行的任务；其他仓库不共享该限制，GitHub 并发组也不保证每个排队任务都被保留或按顺序执行。单次任务上限 30 分钟。每次运行从 `panels/requirements.txt` 安装固定版本依赖到 `.cache/panels/deps`，先执行不使用真实账号的离线回归测试；需要浏览器的模式还执行本地模拟站点的 Chrome 测试，测试失败时不执行账号任务。

浏览器配置、临时目录、子进程 HOME/应用数据、依赖和缓存均指向项目内 `.cache/panels/`。浏览器任务还使用独占 `browser.lock`；若异常终止遗留锁文件，应先确认没有仍在运行的 Chrome/签到进程，再处理项目内旧锁，不要自动覆盖或删除其他运行的锁。

Windows 子进程的 `USERPROFILE`、`LOCALAPPDATA`、`APPDATA` 使用一致的标准 `AppData/Local`、`AppData/Roaming` 布局；只改环境变量而没有对应目录会使 Windows known-folder 查询失败，进而被 Chrome 的默认配置目录保护拒绝 CDP。该约束有本机回归测试，不需要借用个人浏览器目录。

删除或清空某站的账号 Secret 可以停用该站；`[]` 也会按空账号列表跳过。禁用整个 `panels-checkin` 工作流会同时停止两个站点。

### 奖励分类与低奖励提醒

日志和通知保留服务端本次奖励，另外区分以下结果；累计积分仍来自后续积分接口，不能混作本次奖励：

| 分类 | 含义 |
|---|---|
| `reward`（首次奖励） | 返回明确的 `Checkin! Got N Points`，记录本次奖励数值。 |
| `free_reward`（免费账号奖励） | Railgun 返回带免费账号精确后缀的成功文案，记录本次奖励。 |
| `observation`（今日观察已记录） | 返回 `Today's observation logged...`，不推断本次新增积分。 |
| `repeated`（重复签到） | 返回明确的重复签到或今日已签到文案，不推断本次新增积分。 |

只展示安全业务码、白名单 `reason` 和实际签到域名。业务码缺失显示 `missing`，格式异常显示 `invalid`；`reason` 缺失显示 `missing`，不在白名单中的值显示 `other`，不会原样转发服务端文本。成功后的查询失败仍保留已取得的奖励分类，不再次签到。只读诊断不获取这些签到结果。

GLaDOS 的 `reward` 不超过阈值时，日志和通知增加“低奖励提醒”，**不因此标记失败、不重试或自动补签，也不认定原因是 IP 或风控**。Variable `GLADOS_LOW_REWARD_THRESHOLD` 默认 `2`，可设 `0` 到 `100` 的整数；`0` 关闭提醒，空值、非整数或越界使用默认值。Railgun、重复签到和观察记录不触发低奖励提醒。回归测试见 `test_outcomes.py`。

### GLaDOS 可选代理 / WARP 入口

默认不启用显式代理。浏览器模式及其辅助 HTTP 请求明确直连，不继承环境代理；旧 HTTP 方式保留原环境代理行为。Secret `GLADOS_PROXY_URL` 非空时，GLaDOS 浏览器、登录 API、challenge、签到、状态、积分及备用域名请求统一使用该代理；所有 GLaDOS 账号共用同一入口，不提供每账号独立出口。Railgun、其他签到站点、Gmail IMAP 和通知渠道不受该配置影响。

支持 `http://`、`https://`、`socks5://`、`socks5h://`，必须有主机名和有效端口（如果指定），不能含原始空白、业务路径、查询参数或片段。需要代理认证时，用户名和密码中的特殊字符应做 URL 百分号编码，并将完整地址保存为 Secret，不要写进公开 Variable、日志或 Issue。无效配置在创建 Session 前失败，错误及诊断不输出代理地址或凭据。

Chrome 不支持这里的 SOCKS 用户名/密码认证：浏览器模式遇到带认证 SOCKS 地址会明确失败，不静默切回直连。HTTP/HTTPS 认证信息通过浏览器上下文配置传递，不放到 Chrome 启动命令；`socks5h` 在浏览器侧使用 SOCKS5。

例如，**实际运行器内已经存在**监听 `127.0.0.1:40000` 的 SOCKS5 代理时，才可使用：

```text
socks5h://127.0.0.1:40000
```

GitHub 托管运行器的 `127.0.0.1` 不是你的本机；只在自己的电脑上启动 WARP，再填上述地址，不会让云端签到使用 WARP。代理必须部署在实际运行器中或是运行器可以访问的受控服务。显式代理模式关闭环境代理继承，避免 `NO_PROXY` 意外绕过配置；代理出错不会静默回退直连，仍按原请求策略有限重试或报错。

**此入口只使用现有代理，不安装或启动 WARP、不修改系统路由、不验证 WARP 身份，也不保证奖励提高。** 是否低奖励与出口 IP 有关仍需跨天对比，不能靠代理连通或一次签到分值确认。首次启用可先使用 `diagnose_only` 核对登录状态；它仍只查询状态，不签到、不补差。回归见 `test_proxy.py`，包括只访问回环服务的真实代理路由与失败测试。

### 请求失败重试

以下策略适用于旧 HTTP 方式和浏览器成功后的辅助读请求，两个站点共用。**浏览器签到 POST 不使用这套重试策略**：提交后未知结果停止，不重新签到；邮箱登录 API 也不重试。可在 **Actions → Variables** 设置：

| Variable | 默认值 | 有效范围 | 含义 |
|---|---|---|---|
| `PANEL_MAX_ATTEMPTS` | `3` | `1` 到 `5` | 单个接口的最大尝试轮数；GLaDOS 每轮最多访问三个候选域名，每次签到均重新获取 challenge。设为 `1` 关闭轮间重试，仍可切换备用域名。 |
| `PANEL_RETRY_DELAY_SECONDS` | `10` | `0` 到 `60` | 初始等待秒数，后续按指数退避，单次等待最多 60 秒。 |

未配置、非整数或越界时使用默认值。默认最多尝试 3 轮，轮间分别等待 10 秒和 20 秒。Railgun 每轮仅请求一次；GLaDOS 每轮在允许切换时依次尝试候选域名，单个接口最多 9 次尝试（签到每次包含 challenge GET，获得有效 challenge 后才 POST）。

- 仅重试超时、部分连接/传输错误，以及 HTTP `408`、`429`、`500`、`502`、`503`、`504`。
- HTTP `401` / `403`、`code=-2`、设备校验拒绝、配置错误、证书校验错误和未知业务响应不重试。重复签到、今日已记录也不会额外重试。
- `Retry-After` 支持秒数和 HTTP 日期，不会早于服务端要求重试；若要求等待超过 60 秒，停止本次重试，避免长时间阻塞。
- 签到 POST 超时可能是“服务端已处理但响应未收到”，重试可能返回重复签到。耗尽重试时状态仍未知，应核对官网记录，不把它判断为当天一定未签。
- 重试使用同一账号 Session 和 UA；GLaDOS 仅按下述规则切换候选域名。有效响应下发的 Cookie 更新按域名隔离，仅用于本次运行，不更新 Secret，也不自动重新登录。

这里是请求层重试，不是把整个账号签到流程重复执行。认证、设备拒绝、其他不可重试失败或重试耗尽后，停止该账号后续接口请求，继续处理下一个账号。成功签到后的查询失败仍会保留签到成功信息，但整体任务返回失败。

### GLaDOS 备用域名

参考 [lankerr/2026-glados-checkin 的域名列表和请求逻辑](https://github.com/lankerr/2026-glados-checkin/blob/8649193124403fb0195c66a6e4be8b7d2103b96b/checkin.py#L35)，默认按 `glados.cloud`、`glados.rocks`、`glados.network` 的顺序尝试。每个账号从主域名开始，切换成功后，后续状态和积分查询优先使用该域名；不会改变其他账号或 Railgun 的配置。

下述详细规则为旧 HTTP 请求层规则。浏览器 Cookie 模式仅在提交前浏览器/导航请求异常时可换备用域名，换域名后重新加载官网挑战；无效挑战、身份不符、验证拒绝以及已提交的未知结果不切换。邮箱模式不切换本轮登录/浏览器签到域名，避免跨域使用缓存；成功后的辅助 GET 查询仍按 HTTP 规则处理。

- DNS、连接/传输临时错误、超时，以及 HTTP `408` / `500` / `502` / `503` / `504` 可切换备用域名。HTTP `301` / `302` / `303` / `307` / `308` / `404` / `410` 和 GET 返回非 JSON / 非对象时也可切换；不跟随响应的 `Location`，只访问代码中列出的候选域名。
- HTTP `429` 不切换；有效 `Retry-After` 优先按原规则等待或停止，不通过切换绕过等待。认证、设备拒绝、会话被清除、证书错误、未知业务响应及无效 challenge 不触发切换。POST 返回格式异常时停止，避免把状态未知的响应当作域名失效。
- 切换后重新获取该域名的 challenge、重新计算 proof 并等待，POST 的地址、`Origin`、`Referer` 和 `token` 均绑定到发放 nonce 的域名；不会把旧 nonce/proof 重放到备用域名。普通静态 POST 不跨域重放，challenge 失败也不会降级旧协议。
- 每轮每个候选域名最多尝试一次。只读诊断也支持切换，但仍只查询状态；成功签到后的查询切换不会重新签到。各域名以账号配置的原始 Cookie 初始化，服务端更新的 Cookie 只在该域名复用，不复制到其他域名。是否能跨域登录仍由服务端决定。

切换日志仅记录候选域名、接口路径和固定错误分类，不输出 Cookie、nonce 或 proof。回归测试见 `test_domains.py`。

### GLaDOS 一次性 challenge

GLaDOS 旧 HTTP 方式已适配一次性 challenge，参考 [lankerr 的 PR #31](https://github.com/lankerr/2026-glados-checkin/pull/31) 和 [PR #33](https://github.com/lankerr/2026-glados-checkin/pull/33)：同一账号 Session 先 GET `/api/user/checkin/challenge`，读取 `nonce` 和 `ttl`，按 `SHA256(nonce + ".glados-observe-v1")` 计算 proof，等待 2.7 秒，再向同一域名 POST `token`、`nonce`、`proof`。这一路径不提供 Turnstile 验证；若官网要求人机校验，应使用浏览器方式，而不是反复切回旧协议。Railgun 不启用这一步，保留原协议。

`ttl` 单位为毫秒，仅缺失时使用 300000；必须是有限数值且大于 2700。nonce 必须是非空字符串且不超过 4096 字符，按原值 UTF-8 编码，不解码或改写。使用单调时钟核对有效期，并保守计入获取挑战的请求耗时；过期的挑战不会提交。

每次可重试失败或等待中挑战过期时，重新获取 nonce、重新计算 proof 和等待；不直接重放旧 POST，也不叠加挑战 GET 的内部重试。认证、设备拒绝、未知业务响应及无效挑战仍停止账号处理。挑战失败不降级为仅提交 token 的签到，避免静默进入可能只有 1 分的旧路径；日志不输出 nonce/proof。

正常结果仍继续查询账号和积分，并按服务端实际奖励展示。接入 challenge 不保证固定奖励，当天已入账的 1 分不会因重复签到补差。`--diagnose` 仍只查询登录状态，完全不获取 challenge、不签到、不推送。回归见 `test_challenge.py`，覆盖等待、TTL、fresh nonce、同域同 Session、Cookie 更新、失败停止和 Railgun/只读诊断隔离。

## 6. Cookie 形式变化与当前程序边界

**仅 Cookie 字段名变化，不需要改程序。** 脚本按请求 Cookie 字段传递，不强制改名为 `koa:sess` 或 `gld:sess`，也不解码或重签会话内容。会检查格式、重复字段，以及 `*:sess` / `*:sess.sig` 签名对是否完整：

- 新版 `gld:sess` / `gld:sess.sig` 可以原样发送。
- 旧版 `koa:sess` / `koa:sess.sig` 也可以原样发送，但是否仍有效由服务端决定。
- Cookie 中其他字段、签名和不透明值必须原样保留，不能简单把 `koa:` 改成 `gld:` 来恢复旧凭据。

若“形式变化”指 Cookie-Editor JSON 导出或站点新增必需的认证标头，则是不同的问题：账号字段只支持完整 Cookie 请求头字符串，兼容 `Cookie:` 前缀，但不转换导出数组/对象，也没有 `Authorization` 配置入口。

**旧 HTTP / 无邮箱模式只保存本轮更新：** 有效、未被认证或设备拒绝的 JSON 响应下发的 `Set-Cookie` 会按域名、路径和过期时间合并到该账号内存 CookieJar，供后续请求使用。会话或签名被删除、过期时停止后续请求。不更改原账号 JSON、写回 GitHub Secret 或输出 Cookie 值。浏览器邮箱模式的跨次加密缓存及有限登录恢复见第 4 节。

只接收服务端主动下发的更新并非保证自动续期。未启用邮箱恢复时，下一次工作流仍从原 Secret 加载；浏览器显示的半年有效期也不保证固定复制值不会被服务端撤销或替换。失效凭据需要手动更新或使用可选邮箱恢复；隔离 Session、UA 配置和重试本身不能修复 `code=-2`。

## 7. 常见问题

| 现象 | 含义及处理 |
|---|---|
| 未配置、账号列表为空、无 Cookie | 检查对应 Secret 名称、是否填写完整 JSON 数组，以及每个账号的 `cookie` 字符串。 |
| 账号配置解析失败 | 检查 JSON 双引号、逗号、转义和数组结构；不要把代码围栏或 Cookie-Editor 导出数组当作账号配置。 |
| `没有权限`、`code=-2` | 服务端没有接受该次请求的认证；不等同于能证明 Cookie 自然到期。先确认浏览器的同站点状态查询正常，再核对完整 Cookie、UA，以及浏览器是否已更新会话。 |
| 状态接口未返回有效账号数据 | 状态响应缺少脚本预期的 `data` 或数值格式异常。HTTP 200 也可能包含业务错误，任务会失败。 |
| `device-mismatch`、`Automated check-in detected` | 浏览器方式可更新 Cookie 或配置邮箱恢复；旧 HTTP 方式核对 Cookie 与 UA 是否来自同一浏览器平台。只读账号查询成功不保证签到设备校验通过。 |
| `reason=turnstile`、`Cloudflare verification failed`、人机校验超时 | 浏览器等待官网验证，不就绪时不签到；服务端仍拒绝时停止，不发验证码或重放签到。`verify_only` 可用于已有会话的验证排查，不能保证签到或积分恢复。 |
| `Native Chrome/Edge not found`、浏览器锁/配置错误 | 确认运行环境已安装 Chrome，检查项目内 runtime 路径和旧锁；不要将运行目录改到项目外或自动删除其他进程的锁。 |
| `Checkin Repeats! Please Try Tomorrow` | 重复签到，不应反复重试。 |
| GLaDOS 每个账号都获得 2 分或出现“低奖励提醒” | 明确奖励仍算签到成功，不自动补签。核对 UA 来源、规范化提示、传输预设和网络模式，并对照官网记录及跨天结果；不能仅凭这个分值认定原因是 IP、设备校验或平台统一降分。 |
| `Today's observation logged. Return tomorrow for more points.` | 今日已记录，脚本按“今日已签到”展示，不代表每次调用都会增加积分。 |
| `Already checked in today. Come back tomorrow.` | Railgun 的“今日已签到”响应；与 `code=1` 一起返回时也是正常结果，无需更换 Cookie 或重试。2026-11-04 实测确认登录及积分查询有效，旧版因未识别这条文案而误报失败。 |
| `Checkin! Got 2 Points (Free account - upgrade for more!)` | Railgun 免费账号的成功响应，2026-11-06 的[公开日志](https://github.com/zzzmzz/railgun_checkin/actions/runs/42180464258)已记录 `code=0`。仅 Railgun 且 `code=0` / `"0"` 时接受这个精确后缀，积分取文案中的数值，并标注“免费账号奖励”；旧版要求文案在 `Points` 处结束，会误报失败并停止后续查询。其他后缀、缺失或失败业务码仍失败，认证和设备拒绝检查保持优先。 |
| 剩余天数或积分未获取 | 请求失败会停止后续接口，未取得的值显示“未获取”；只有接口确实返回零积分才显示 `0`。 |
| Actions 红色，但有账号签到成功 | 任一账号、后续查询或通知失败都会标红；查看安全错误分类和通知中的各账号结果，不代表所有账号都失败。 |
| 通知去了旧企微群 | 检查是否保留 `PUSH_WEBHOOK_URL`，并设置对应站点的 `*_WECOM_WEBHOOK`。 |
| 已填代理但请求异常 | 确认代理可从实际运行器访问；云端 `127.0.0.1` 不指向本机。显式代理故障不会自动直连，删除或清空 `GLADOS_PROXY_URL` 才恢复默认网络行为。 |

2026-11-03 的只读排查已确认当时仓库中的 4 份 GLaDOS 凭据均含完整新版字段，但状态和积分接口仍返回 `code=-2`，且没有下发 `Set-Cookie`。因此，字段名已更新本身不能解决那次认证失败；现有证据也不能确定是正常到期、撤销还是旧会话被替换。

本地运行建议使用 Python 3.11 或更新版本，浏览器方式还需已安装的 Chrome。依赖由 `panels/requirements.txt` 固定，包含 `requests`、`curl_cffi`、`playwright`、`cryptography`；建议 `pip --target .cache/panels/deps` 安装并设置 `PYTHONPATH` 指向该项目内目录，同时将 pip cache 和 `TEMP` / `TMP` / `TMPDIR` 指向项目内已创建的目录。不要把依赖、临时配置或下载放到项目外。运行前设置对应环境变量，浏览器本地运行额外设 `GLADOS_BROWSER_CHECKIN=1`。`panels/checkin.py` 不自动读取 `.env`。默认本地运行仍会真实签到和推送。只读命令从仓库根目录执行：

```shell
python -B panels/checkin.py --diagnose
python -B panels/checkin.py --verify-browser
```

也可设置 `PANEL_DIAGNOSTIC_ONLY=true` 或 `GLADOS_VERIFY_ONLY=true`，两者互斥。无法识别的布尔值会直接失败，不意外执行签到。两种诊断都使用账号凭据，但不发送应用签到 POST。

只运行离线回归测试时，从仓库根目录执行以下命令，不使用真实 Cookie、不联网签到。已安装 `curl_cffi` 时，请求头和代理回归会启动仅绑定 `127.0.0.1` 的临时 HTTP 服务；未安装时这些真实传输测试跳过，不能替代依赖齐全时的验证：

```shell
python -B -m unittest discover -s panels -p "test_*.py"
```

额外设置 `GLADOS_BROWSER_TESTS=1` 可执行真实原生 Chrome 的回环模拟测试，验证一次 POST、拦截其他应用写请求、账号隔离及只读验证。模拟站点只监听 `127.0.0.1`，使用虚构 Cookie，不访问 GLaDOS/邮箱；浏览器 headless 模式的临时配置和测试产物限定在 `.cache/panels/`。这类测试通过不等于你的真实账号已签到。
