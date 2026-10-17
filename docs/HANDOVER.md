# wan3-canvas 维护交接

> 给接手维护的人。**先读这页，再读 `docs/dev-notes.md`（40 条踩坑记录）**，
> 改代码前必看。其它文档（README / CHANGELOG）按需查。

## 1. 项目

| | |
|---|---|
| 路径 | `D:\文档\github好的项目\wan3画布\` |
| 仓库 | `https://github.com/xiya-like/wan3-canvas`（master） |
| 是什么 | 复刻「千问创作」workflow 页：原生 JS + SVG 节点画布，按量付费调百炼生成视频/图片 |
| 运行 | `python backend.py` → `http://127.0.0.1:8000` |

## 2. 硬约束（违反就是错）

1. **禁止发起任何真实付费 API 调用**（视频/图像生成），除非用户明确批准。
   验证一律用 mock urlopen / 只读接口 / 无头浏览器 DOM 断言。用户原话：
   「不要再发送视频生成请求了，我的 api 没钱」。
   *例外*：文件上传接口官方免费，可用于验证；`GET /api/v1/files/{id}` 是只读的。
2. **前端零外部依赖**（原生 JS + SVG，无框架无 CDN）；**后端仅 Python 标准库**。
3. 改代码必须同步更新 `README.md` / `CHANGELOG.md` / `docs/dev-notes.md`。
   **写入一律用 `edit` 追加/定点替换，禁止整文件重写**（CHANGELOG 571 行，重写一次白烧几千 token）。
4. 公开仓库，`LICENSE` 为 MIT。

## 3. 文件地图

| 文件 | 内容 |
|---|---|
| `backend.py` | 全部后端。纯标准库 HTTP 服务 + DashScope 代理 |
| `index.html` | 全部前端。约 110KB，零依赖 |
| `test_*.py` | 五套自测，见下 |
| `docs/dev-notes.md` | **40 条踩坑记录，改代码前必读** |
| `CHANGELOG.md` | 变更记录（修订一～二十四） |
| `README.md` | 用户文档 |

后端：价格表 `VIDEO_COST` / `IMAGE_COST` 集中在文件头；模型与规格上限同处。
前端：`role(n)` 推导块角色 → `bodyHTML(n)` 渲染 → `mirrorText(n)` 实时镜像上游文本。

### 行号锚点（2026-10-17 标定；代码变动后会漂移，漂移了就重新 grep）

**禁止整文件读 `index.html`（114K 字符）和 `test_ui.py`（67K）**。
先 `grep` 定位，再用 `read` 的 `offset`/`limit` 只读目标区间（一般 ±50 行足够）。

| index.html | 行 | backend.py | 行 |
|---|---|---|---|
| `DEFAULT_NEG` 常量 | 324 | `IMAGE_COST` / `size_class()` | 120 / 128 |
| `role(n)` | 368 | `VIDEO_COST` | 155 |
| `mirrorText(n)` | 447 | `ds_request()`（请求核心） | 196 |
| `newNode()`（imgParams 默认值 758） | 754 | `upload_to_dashscope()` | 256 |
| `bodyHTML(n)` | 1055 | `submit_video()` / `submit_image()` | 324 / 359 |
| `_rendering` 闸 + `render()` | 1297 | `query_task()` | 438 |
| `exportWf()` / `importWf()` | 2250 / 2330 | `Handler` 路由 | 444 |
| | | `purge_uploads()` / `main()` | 774 / 801 |

## 4. 测试

```powershell
python test_backend.py   # 166  mock urlopen，断请求体与门禁
python test_upload.py    #  20  真上传链路（只写本地）
python test_media.py     #   9  播放不被打断
python test_ui.py        # 301  无头浏览器 DOM 断言
python test_server.py    #  19  真启动服务
```
合计 **515 项**，全绿。改完必须全跑一遍。

### 跑法（省 token，必守）

- 五套输出冗长，**别让输出直接进对话**：写个 `.ps1` 顺序跑五套，
  `*> $null` 吞掉输出，最后只打印每套 exit code 一行（如 `test_ui: 0`）。
- **全绿**：只回报一行结论，不贴任何测试输出。
- **有红**：只读失败那套输出的**尾部**（`Get-Content -Tail 50`）定位，不要整段贴回来。
- 注意下节红线依旧有效：绝不能用 `> test_*_out.txt` 重定向（管道占文件会 PermissionError），
  吞输出用 `*> $null`，不写文件。

### 测试的坑（这几条踩过很多次，见 dev-notes）

- **绝不要把测试输出重定向到 `test_*_out.txt`**（`> file`）。管道先占住文件，
  测试内部再 `open()` 会 PermissionError，五套一起 exit=1。让测试自己写文件。
- 测试临时文件一律 `tempfile.mkdtemp(prefix="wan3_*")` + `finally` 清理，**别留项目目录**。
- 探针输出别 `print` 到 Windows 控制台（GBK，emoji/`¥` 会 UnicodeEncodeError），
  先写 UTF-8 文件再读。
- 探针分隔符是 `@@`；`<pre id="probe">` 必须在 `<body>` 开头就存在。
- 断言任何「mousedown 里可能动 DOM」的交互，**必须额外查 `el.isConnected`**。
- **禁止用 `dispatchEvent(new MouseEvent('dblclick'))`**——浏览器压根不派发它，会假绿。
  要派发两轮完整 `mousedown → mouseup → click`。
- 新加类名前先 grep 全文件。`.lb` 已被灯箱占用，用 `.mLb`。
- 测试失败先想「是不是代码错了」，别直接改断言迁就 mock。
  出现意外 0/None 时先查为什么是 0（mock 造不出真实场景的信号）。

## 5. 终端规则

- 一律 `pwsh` 7.x。**超过一行先写成 `.ps1` 文件**（用 write 工具写），
  再 `pwsh -NoProfile -File x.ps1`。
- 绝不用 bash 语法（heredoc、反斜杠续行）。
- 内联单行命令避开双引号用单引号——bash 工具的 5.1 外层会吃掉内层引号。
- `Set-Location` **不改 .NET 进程 CWD**。`.NET` 文件 API 传绝对路径；
  造文件的验证脚本直接写 Python（`os.path.abspath(__file__)` 派生路径）。
- 删文件走顶层 `rm -- "<path>"`（可恢复）。包在 pwsh 里的 `rm` 会被安全策略硬拦。
- gh/git 网络命令前设 `$env:HTTPS_PROXY`（用户机器 Clash 在 127.0.0.1:10808，
  但 git 不读系统代理）。**报 `error connecting to api.github.com` 时不要误判成 token 失效**。

## 6. 当前状态

- 工作区干净，本地**领先远端 5 个提交**（`c568642` → `99b95f1`）。
  推送被网络阻塞（Clash 未监听，直连 github.com:443 也失败），**用户说先不推，等他提醒**。
- API Key 在**用户环境变量** `DASHSCOPE_API_KEY`（项目里没有 `.env`）。
- `DASHSCOPE_BASE` 未设 → 默认北京地域。**文件管理 API 只在北京 Region 开放**，
  换地域会「上传成功但查不到 url」。

## 7. 易踩的运行时坑

- **改完 `backend.py` 必须重启进程**，Python 不热重载。曾因此误判「修复没生效」。
- 改完 `index.html` 要浏览器**硬刷新 Ctrl+F5**。
- `render()` 会销毁重建所有节点（`<video>`/`<audio>` 也在内）——播放器被重置的元凶。
  有 `_rendering` 闸防重入，交互路径少调 `render()`，优先软更新。
- `newNode()` 已经 `S.nodes.push()`，调用方不要再 push（导入 3 个块会变 6 个）。
- 不能拿 `newNode()` 当导入模板——它会把模版块本身 push 进数组。用完立刻 `pop()`。
- 工作流 JSON 迁移只补顶层 `undefined` 键；**`imgParams` 是嵌套的，要单独补**。

## 8. 待办 / 悬而未决

- 推送上述 5 个提交（等用户开口）。
- `size_class()` 把 `1440x1440`（207 万像素）归到 1K，沿用既有判定。
  严格按像素更像 2K，会让 3.0-pro 从 ¥0.25 跳到 ¥0.50。
  **未确认官方如何定义 1K/2K 边界**，不要擅自改。
- `DEFAULT_NEG`（负向提示词默认英文串）会写进导出的工作流 JSON。
  用户若嫌分享出去带一大段英文，可改成「新建为空，点一下才填」。
- 原站「成本显示 `¥45`」单位未知（可能是积分），未对上，见 README「已知取舍」。
- 图片连入视频块时本项目仍解析为 t2v，原站同一情形显示 omni 占位符，待确认。

## 9. 权威资料

- 定价：百炼控制台价格表（2026-10-17 核对）。视频标准版当时**仍在 7 折活动期**
  （0.21/0.42/0.84，原价 0.3/0.6/1.2），Prime 无折扣（0.45/0.9/1.8）。
- `negative_prompt`：官方只排除 `wan2.7-image-pro` 和 `wan2.7-image`，
  **其余全部支持**（曾误写「仅 3.0 系列」，已在修订二十四改正）。
- 上传接口：`POST /api/v1/files` 只回 `file_id`，
  要用 `GET /api/v1/files/{file_id}` 换 `data.url`（带签名 OSS 直链，约 24h 有效）。
