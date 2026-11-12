# GLaDOS / Railgun 配置指南

两个站点共用 `checkin.py` 和 GitHub Actions 工作流 `panels.yml`，账号配置独立，未配置的站点自动跳过。通知渠道由仓库根目录的 `notify.py` 统一处理。

## 1. 配置入口

在自己的仓库打开 **Settings → Secrets and variables → Actions**。

| 站点 | 当前接口域名 | Secrets：账号配置 | Variables：浏览器 UA（可选） |
|---|---|---|---|
| GLaDOS | `https://glados.cloud`（支持备用域名，见下文） | `GLADOS_ACCOUNTS_JSON` | `GLADOS_USER_AGENT` |
| Railgun | `https://railgun.info` | `RAILGUN_ACCOUNTS_JSON` | `RAILGUN_USER_AGENT` |

- 账号配置和推送密钥放在 **Secrets → New repository secret**，不要放进公开 Variables、代码或 Issue。
- 面板 User-Agent 可放在 **Variables → New repository variable**，或同名 Secret；工作流优先读取非空 Variable，未设置时回退同名 Secret。账号级 `user_agent` 仍优先于面板配置。
- 只使用一个站点时，仅配置该站点的账号 Secret，另一个留空。
- Fork 用户先同步仓库代码，再填写自己的配置；同步代码不会同步或更新 Secrets。

## 2. 获取 Cookie 和 User-Agent

1. 用平时使用的浏览器打开对应站点并登录，确认控制台可以查看账号信息。
2. 按 F12 打开开发者工具，切换到 **Network / 网络**，刷新控制台页面。
3. 找到对应站点的 `/api/user/status` 请求。先查看响应 JSON，确认有账号 `data`；仅有 HTTP 200 不代表登录成功。
4. 在 **Request Headers / 请求标头** 中复制 `Cookie` 的完整值，包括所有字段名、等号、值和分号。脚本也兼容带 `Cookie:` 前缀的整行。
5. 从同一条请求复制完整的 `User-Agent`，填入该站点的 UA Variable，或对应账号的可选 `user_agent` 字段。

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

`email` 只是通知中显示的账号标识，不参与服务端认证；可以使用便于识别的账号名称，省略时显示“账号1”等默认标识。`cookie` 才是登录凭据，必须是字符串。

推荐直接粘贴标准 JSON 数组，不加 Markdown 围栏、不额外包一层引号。JSON 字符串内的双引号和反斜杠需要转义；字符串值内不要插入实际换行。外层数组可以正常缩进换行。脚本虽兼容双重 JSON 编码，首次配置无需使用它。

## 4. User-Agent 和通知

**原有 `email` + `cookie` 配置继续有效，不需要批量重配。** 默认使用固定 Windows Chrome 124 UA，与默认 `chrome124` 传输预设一致。不自动改写已配置 UA 的浏览器平台或版本，只移除复制时附带的 `User-Agent:` 前缀、成对外层引号及两端空格。控制字符、无法发送的字符、空值包装或重复包装会在创建 Session 前拒绝，错误不输出原始值。

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

每个账号拥有独立的 HTTP Session 和 CookieJar，同账号的签到、状态、积分和重试复用该 Session，处理结束后关闭。**这不是独立真实浏览器，也不提供不同出口 IP**；GitHub Actions 上的多个账号通常仍共用运行器网络。不要把随机 UA 或 Session 隔离视为能消除风控。

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

1. 打开 **Actions → panels-checkin → Run workflow**，选择自己仓库的主分支运行。首次排查建议勾选 `diagnose_only`。
2. 查看 `Run checkin` 步骤和通知中的实际结果，分别确认两个站点是否被跳过或执行。
3. 核对官网的签到记录、积分和剩余天数，不要只看工作流的绿色成功标记。

默认运行会实际发送签到请求，并向已配置渠道推送。勾选 `diagnose_only` 后只 GET `/api/user/status`，不签到、不查询积分、不推送、不修改 Secrets；只读诊断成功也不保证签到设备校验通过。

任一账号的签到或查询失败、配置异常或已配置通知渠道失败都会使脚本返回非零状态，工作流标红；其他账号和站点仍继续处理。未配置站点或空数组不算失败。日志只展示账号序号和安全错误分类，不输出邮箱、Cookie、原始服务端错误文本或通知密钥；通知仍使用配置中的账号标识。

定时为 UTC 的 `0 */8 * * *`，对应北京时间约 00:00、08:00、16:00；GitHub 调度可能延迟，并非保证精确到点。本仓库使用同一 `panels-checkin` 并发组，跨分支的定时、手动和只读诊断运行也互斥，不取消正在运行的任务；其他仓库不共享该限制，GitHub 并发组也不保证每个排队任务都被保留或按顺序执行。单次任务上限 20 分钟。每次运行从 `panels/requirements.txt` 安装固定版本的关键依赖，并先执行不使用真实账号的离线回归测试，测试失败时不执行签到或诊断。

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

默认不启用显式代理，保留原运行器网络和环境代理行为。只有在 Secret `GLADOS_PROXY_URL` 非空时，GLaDOS 的 challenge、签到、状态、积分、重试及备用域名请求才统一使用该代理；所有 GLaDOS 账号共用同一入口，不提供每账号独立出口。Railgun、其他签到站点和通知渠道不受该配置影响。

支持 `http://`、`https://`、`socks5://`、`socks5h://`，必须有主机名和有效端口（如果指定），不能含原始空白、业务路径、查询参数或片段。需要代理认证时，用户名和密码中的特殊字符应做 URL 百分号编码，并将完整地址保存为 Secret，不要写进公开 Variable、日志或 Issue。无效配置在创建 Session 前失败，错误及诊断不输出代理地址或凭据。

例如，**实际运行器内已经存在**监听 `127.0.0.1:40000` 的 SOCKS5 代理时，才可使用：

```text
socks5h://127.0.0.1:40000
```

GitHub 托管运行器的 `127.0.0.1` 不是你的本机；只在自己的电脑上启动 WARP，再填上述地址，不会让云端签到使用 WARP。代理必须部署在实际运行器中或是运行器可以访问的受控服务。显式代理模式关闭环境代理继承，避免 `NO_PROXY` 意外绕过配置；代理出错不会静默回退直连，仍按原请求策略有限重试或报错。

**此入口只使用现有代理，不安装或启动 WARP、不修改系统路由、不验证 WARP 身份，也不保证奖励提高。** 是否低奖励与出口 IP 有关仍需跨天对比，不能靠代理连通或一次签到分值确认。首次启用可先使用 `diagnose_only` 核对登录状态；它仍只查询状态，不签到、不补差。回归见 `test_proxy.py`，包括只访问回环服务的真实代理路由与失败测试。

### 请求失败重试

签到、状态和积分接口共用有限重试策略，可在 **Actions → Variables** 设置以下变量，两个站点共用：

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

- DNS、连接/传输临时错误、超时，以及 HTTP `408` / `500` / `502` / `503` / `504` 可切换备用域名。HTTP `301` / `302` / `303` / `307` / `308` / `404` / `410` 和 GET 返回非 JSON / 非对象时也可切换；不跟随响应的 `Location`，只访问代码中列出的候选域名。
- HTTP `429` 不切换；有效 `Retry-After` 优先按原规则等待或停止，不通过切换绕过等待。认证、设备拒绝、会话被清除、证书错误、未知业务响应及无效 challenge 不触发切换。POST 返回格式异常时停止，避免把状态未知的响应当作域名失效。
- 切换后重新获取该域名的 challenge、重新计算 proof 并等待，POST 的地址、`Origin`、`Referer` 和 `token` 均绑定到发放 nonce 的域名；不会把旧 nonce/proof 重放到备用域名。普通静态 POST 不跨域重放，challenge 失败也不会降级旧协议。
- 每轮每个候选域名最多尝试一次。只读诊断也支持切换，但仍只查询状态；成功签到后的查询切换不会重新签到。各域名以账号配置的原始 Cookie 初始化，服务端更新的 Cookie 只在该域名复用，不复制到其他域名。是否能跨域登录仍由服务端决定。

切换日志仅记录候选域名、接口路径和固定错误分类，不输出 Cookie、nonce 或 proof。回归测试见 `test_domains.py`。

### GLaDOS 一次性 challenge

GLaDOS 已适配官网新版流程，参考 [lankerr 的 PR #31](https://github.com/lankerr/2026-glados-checkin/pull/31) 和 [PR #33](https://github.com/lankerr/2026-glados-checkin/pull/33)：同一账号 Session 先 GET `/api/user/checkin/challenge`，读取 `nonce` 和 `ttl`，按 `SHA256(nonce + ".glados-observe-v1")` 计算 proof，等待 2.7 秒，再向同一域名 POST `token`、`nonce`、`proof`。原有账号、UA 和 Secrets 配置无需改变。Railgun 不启用这一步，保留原协议。

`ttl` 单位为毫秒，仅缺失时使用 300000；必须是有限数值且大于 2700。nonce 必须是非空字符串且不超过 4096 字符，按原值 UTF-8 编码，不解码或改写。使用单调时钟核对有效期，并保守计入获取挑战的请求耗时；过期的挑战不会提交。

每次可重试失败或等待中挑战过期时，重新获取 nonce、重新计算 proof 和等待；不直接重放旧 POST，也不叠加挑战 GET 的内部重试。认证、设备拒绝、未知业务响应及无效挑战仍停止账号处理。挑战失败不降级为仅提交 token 的签到，避免静默进入可能只有 1 分的旧路径；日志不输出 nonce/proof。

正常结果仍继续查询账号和积分，并按服务端实际奖励展示。接入 challenge 不保证固定奖励，当天已入账的 1 分不会因重复签到补差。`--diagnose` 仍只查询登录状态，完全不获取 challenge、不签到、不推送。回归见 `test_challenge.py`，覆盖等待、TTL、fresh nonce、同域同 Session、Cookie 更新、失败停止和 Railgun/只读诊断隔离。

## 6. Cookie 形式变化与当前程序边界

**仅 Cookie 字段名变化，不需要改程序。** 脚本按请求 Cookie 字段传递，不强制改名为 `koa:sess` 或 `gld:sess`，也不解码或重签会话内容。会检查格式、重复字段，以及 `*:sess` / `*:sess.sig` 签名对是否完整：

- 新版 `gld:sess` / `gld:sess.sig` 可以原样发送。
- 旧版 `koa:sess` / `koa:sess.sig` 也可以原样发送，但是否仍有效由服务端决定。
- Cookie 中其他字段、签名和不透明值必须原样保留，不能简单把 `koa:` 改成 `gld:` 来恢复旧凭据。

若“形式变化”指 Cookie-Editor JSON 导出或站点新增必需的认证标头，则是不同的问题：账号字段只支持完整 Cookie 请求头字符串，兼容 `Cookie:` 前缀，但不转换导出数组/对象，也没有 `Authorization` 配置入口。

**支持本次运行内 Cookie 更新，但不持久化：** 有效、未被认证或设备拒绝的 JSON 响应下发的 `Set-Cookie` 会按域名、路径和过期时间合并到该账号内存 CookieJar，供后续请求使用。会话或签名被删除、过期时停止后续请求。不会更改原账号 JSON、写回 GitHub Secret、重新登录或输出 Cookie 值。

这只能接收服务端主动下发的更新，并非保证自动续期。下一次工作流仍从原 Secret 加载，浏览器显示的半年有效期也不保证固定复制值不会被服务端撤销或替换。失效凭据仍需要重新登录并更新 Cookie；隔离 Session、UA 配置和重试不能修复 `code=-2`。

## 7. 常见问题

| 现象 | 含义及处理 |
|---|---|
| 未配置、账号列表为空、无 Cookie | 检查对应 Secret 名称、是否填写完整 JSON 数组，以及每个账号的 `cookie` 字符串。 |
| 账号配置解析失败 | 检查 JSON 双引号、逗号、转义和数组结构；不要把代码围栏或 Cookie-Editor 导出数组当作账号配置。 |
| `没有权限`、`code=-2` | 服务端没有接受该次请求的认证；不等同于能证明 Cookie 自然到期。先确认浏览器的同站点状态查询正常，再核对完整 Cookie、UA，以及浏览器是否已更新会话。 |
| 状态接口未返回有效账号数据 | 状态响应缺少脚本预期的 `data` 或数值格式异常。HTTP 200 也可能包含业务错误，任务会失败。 |
| `device-mismatch`、`Automated check-in detected` | 核对 Cookie 与 UA 是否来自同一浏览器平台。只读账号查询成功也不保证签到设备校验通过。 |
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

本地运行建议使用 Python 3.11 或更新版本，并从仓库根目录执行 `python -m pip install -r panels/requirements.txt` 安装 `requests`、`curl_cffi` 的固定版本；工作流使用同一文件。安装位置和缓存应保持在自己指定的项目环境中。运行前预先设置对应环境变量。`panels/checkin.py` 不自动读取 `.env`，`nodeseek/.env.example` 也不是面板脚本的自动配置入口。默认本地运行同样会真实签到和推送。只读诊断可从仓库根目录执行：

```shell
python -B panels/checkin.py --diagnose
```

也可设置 `PANEL_DIAGNOSTIC_ONLY=true`。无法识别的布尔值会直接失败，不意外执行签到。诊断仍使用账号凭据访问状态接口，但不发送签到 POST。

只运行离线回归测试时，从仓库根目录执行以下命令，不使用真实 Cookie、不联网签到。已安装 `curl_cffi` 时，请求头和代理回归会启动仅绑定 `127.0.0.1` 的临时 HTTP 服务；未安装时这些真实传输测试跳过，不能替代依赖齐全时的验证：

```shell
python -B -m unittest discover -s panels -p "test_*.py"
```
