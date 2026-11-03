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
4. 在 **Request Headers / 请求标头** 中复制 `Cookie` 的完整值，包括所有字段名、等号、值和分号。不要把前面的 `Cookie:` 标签一起复制进去。
5. 从同一条请求复制完整的 `User-Agent`，填入该站点的 UA Variable。

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

默认使用固定的 Windows Chrome UA。若获取 Cookie 的浏览器在 macOS、Linux、手机或使用其他浏览器，请设置同一浏览器请求里的 UA。每个站点目前只能配置一条 UA，多账号应尽量使用相同设备平台；没有账号级 UA 或自动轮换功能。

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

1. 打开 **Actions → panels-checkin → Run workflow**，选择自己仓库的主分支运行。
2. 查看 `Run checkin` 步骤和通知中的实际结果，分别确认两个站点是否被跳过或执行。
3. 核对官网的签到记录、积分和剩余天数，不要只看工作流的绿色成功标记。

该工作流会实际发送签到请求，并向已配置渠道推送，不是只读诊断。定时为 UTC 的 `0 */8 * * *`，对应北京时间约 00:00、08:00、16:00；GitHub 调度可能延迟，并非保证精确到点。

删除或清空某站的账号 Secret 可以停用该站；`[]` 也会按空账号列表跳过。禁用整个 `panels-checkin` 工作流会同时停止两个站点。

## 6. Cookie 形式变化与当前程序边界

**仅 Cookie 字段名变化，不需要改程序。** `generate_headers()` 将 `cookie` 字符串放进请求标头，不硬编码 `koa:sess`、`gld:sess`，也不解码或重签会话内容：

- 新版 `gld:sess` / `gld:sess.sig` 可以原样发送。
- 旧版 `koa:sess` / `koa:sess.sig` 也可以原样发送，但是否仍有效由服务端决定。
- Cookie 中其他字段、签名和不透明值必须原样保留，不能简单把 `koa:` 改成 `gld:` 来恢复旧凭据。

若“形式变化”指 Cookie-Editor JSON 导出、`Cookie:` 整行文本，或站点新增必需的认证标头，则是不同的问题：当前账号字段只支持完整 Cookie 值字符串，不会自动转换导出格式；脚本也没有 `Authorization` / 浏览器指纹配置入口。需要增加这些能力时才需对应改代码。

**目前没有自动续期和持久化：** 签到、状态、积分请求各自创建 Session，脚本不把响应的 `Set-Cookie` 更新用于后续请求，也不会写回 GitHub Secret。浏览器可能在访问过程中更新会话，浏览器显示的 Cookie 有效期不保证一份固定复制值能一直被服务端接受。支持自动续期需要复用或合并会话，并验证后安全持久化，不能仅改 Cookie 名字。

## 7. 常见问题

| 现象 | 含义及处理 |
|---|---|
| 未配置、账号列表为空、无 Cookie | 检查对应 Secret 名称、是否填写完整 JSON 数组，以及每个账号的 `cookie` 字符串。 |
| 账号配置解析失败 | 检查 JSON 双引号、逗号、转义和数组结构；不要把代码围栏或 Cookie-Editor 导出数组当作账号配置。 |
| `没有权限`、`code=-2` | 服务端没有接受该次请求的认证；不等同于能证明 Cookie 自然到期。先确认浏览器的同站点状态查询正常，再核对完整 Cookie、UA，以及浏览器是否已更新会话。 |
| `状态查询异常: 'data'` | 状态响应缺少脚本预期的 `data`，常见于认证被拒。HTTP 200 也可能包含业务错误。 |
| `device-mismatch`、`Automated check-in detected` | 核对 Cookie 与 UA 是否来自同一浏览器平台。只读账号查询成功也不保证签到设备校验通过。 |
| `Checkin Repeats! Please Try Tomorrow` | 重复签到，不应反复重试。 |
| `Today's observation logged. Return tomorrow for more points.` | 今日已记录，脚本按“今日已签到”展示，不代表每次调用都会增加积分。 |
| 剩余天数获取失败、积分显示 0 | 当前积分异常会回退到 `0`，不应据此认定实际余额为零；以官网和接口认证情况为准。 |
| Actions 绿色，但通知显示失败 | 当前脚本会捕获请求错误，业务失败不一定让进程以非零状态退出；绿色只表示脚本执行完毕。 |
| 通知去了旧企微群 | 检查是否保留 `PUSH_WEBHOOK_URL`，并设置对应站点的 `*_WECOM_WEBHOOK`。 |

2026-11-03 的只读排查已确认当时仓库中的 4 份 GLaDOS 凭据均含完整新版字段，但状态和积分接口仍返回 `code=-2`，且没有下发 `Set-Cookie`。因此，字段名已更新本身不能解决那次认证失败；现有证据也不能确定是正常到期、撤销还是旧会话被替换。

本地运行时需要 `requests`、`curl_cffi`，并预先设置对应环境变量。`panels/checkin.py` 不自动读取 `.env`，`nodeseek/.env.example` 也不是面板脚本的自动配置入口。本地运行同样会真实签到和推送。

只运行离线回归测试时，从仓库根目录执行以下命令，不使用真实 Cookie、不联网签到：

```shell
python -B -m unittest discover -s panels -p "test_*.py"
```
