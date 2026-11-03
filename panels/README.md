# GLaDOS / Railgun 配置指南

两个站点共用 `checkin.py` 和 GitHub Actions 工作流 `panels.yml`，账号配置独立，未配置的站点自动跳过。通知渠道由仓库根目录的 `notify.py` 统一处理。

## 1. 配置入口

在自己的仓库打开 **Settings → Secrets and variables → Actions**。

| 站点 | 当前接口域名 | Secrets：账号配置 | Variables：浏览器 UA（可选） |
|---|---|---|---|
| GLaDOS | `https://glados.cloud` | `GLADOS_ACCOUNTS_JSON` | `GLADOS_USER_AGENT` |
| Railgun | `https://railgun.info` | `RAILGUN_ACCOUNTS_JSON` | `RAILGUN_USER_AGENT` |

- 账号配置和推送密钥放在 **Secrets → New repository secret**，不要放进公开 Variables、代码或 Issue。
- User-Agent 放在 **Variables → New repository variable**；放进同名 Secret 不会被当前工作流读取。
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

**原有 `email` + `cookie` 配置继续有效，不需要批量重配。** 默认使用固定 Windows Chrome 124 UA，与默认 `chrome124` 传输预设一致。已设置的面板 UA 保持原值，不会被自动改写。

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

定时为 UTC 的 `0 */8 * * *`，对应北京时间约 00:00、08:00、16:00；GitHub 调度可能延迟，并非保证精确到点。同分支工作流不并发执行、不取消正在运行的任务；单次任务上限 20 分钟。每次运行先执行不使用真实账号的离线回归测试，测试失败时不执行签到或诊断。

删除或清空某站的账号 Secret 可以停用该站；`[]` 也会按空账号列表跳过。禁用整个 `panels-checkin` 工作流会同时停止两个站点。

### 请求失败重试

签到、状态和积分接口共用有限重试策略，可在 **Actions → Variables** 设置以下变量，两个站点共用：

| Variable | 默认值 | 有效范围 | 含义 |
|---|---|---|---|
| `PANEL_MAX_ATTEMPTS` | `3` | `1` 到 `5` | 单个接口的最大尝试次数，包含首次请求；设为 `1` 关闭重试。 |
| `PANEL_RETRY_DELAY_SECONDS` | `10` | `0` 到 `60` | 初始等待秒数，后续按指数退避，单次等待最多 60 秒。 |

未配置、非整数或越界时使用默认值。默认最多请求 3 次，重试前分别等待 10 秒和 20 秒。

- 仅重试超时、部分连接/传输错误，以及 HTTP `408`、`429`、`500`、`502`、`503`、`504`。
- HTTP `401` / `403`、`code=-2`、设备校验拒绝、配置错误、证书校验错误和未知业务响应不重试。重复签到、今日已记录也不会额外重试。
- `Retry-After` 支持秒数和 HTTP 日期，不会早于服务端要求重试；若要求等待超过 60 秒，停止本次重试，避免长时间阻塞。
- 签到 POST 超时可能是“服务端已处理但响应未收到”，重试可能返回重复签到。耗尽重试时状态仍未知，应核对官网记录，不把它判断为当天一定未签。
- 重试使用同一账号 Session、UA 和域名，不随机切换身份。有效响应下发的 Cookie 更新仅用于本次运行，不更新 Secret，也不自动重新登录。

这里是请求层重试，不是把整个账号签到流程重复执行。认证、设备拒绝、其他不可重试失败或重试耗尽后，停止该账号后续接口请求，继续处理下一个账号。成功签到后的查询失败仍会保留签到成功信息，但整体任务返回失败。

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
| `Today's observation logged. Return tomorrow for more points.` | 今日已记录，脚本按“今日已签到”展示，不代表每次调用都会增加积分。 |
| 剩余天数或积分未获取 | 请求失败会停止后续接口，未取得的值显示“未获取”；只有接口确实返回零积分才显示 `0`。 |
| Actions 红色，但有账号签到成功 | 任一账号、后续查询或通知失败都会标红；查看安全错误分类和通知中的各账号结果，不代表所有账号都失败。 |
| 通知去了旧企微群 | 检查是否保留 `PUSH_WEBHOOK_URL`，并设置对应站点的 `*_WECOM_WEBHOOK`。 |

2026-11-03 的只读排查已确认当时仓库中的 4 份 GLaDOS 凭据均含完整新版字段，但状态和积分接口仍返回 `code=-2`，且没有下发 `Set-Cookie`。因此，字段名已更新本身不能解决那次认证失败；现有证据也不能确定是正常到期、撤销还是旧会话被替换。

本地运行时需要 `requests`、`curl_cffi`，并预先设置对应环境变量。`panels/checkin.py` 不自动读取 `.env`，`nodeseek/.env.example` 也不是面板脚本的自动配置入口。默认本地运行同样会真实签到和推送。只读诊断可从仓库根目录执行：

```shell
python -B panels/checkin.py --diagnose
```

也可设置 `PANEL_DIAGNOSTIC_ONLY=true`。无法识别的布尔值会直接失败，不意外执行签到。诊断仍使用账号凭据访问状态接口，但不发送签到 POST。

只运行离线回归测试时，从仓库根目录执行以下命令，不使用真实 Cookie、不联网签到：

```shell
python -B -m unittest discover -s panels -p "test_*.py"
```
