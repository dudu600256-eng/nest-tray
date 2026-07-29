# Note Tray 实施计划

> 基于 `docs/superpowers/specs/2026-07-26-note-tray-design-v2.md`
> 日期: 2026-07-26
> 最后更新: 2026-07-26 (架构修正版)

---

## 总体依赖与里程碑

```
阶段1 ──→ 阶段2 ──→ M1(打包验证) ──→ 阶段3
    ↘                              ↓
     阶段5 ──────────→ M3(闭环) ←───┘
                         ├──→ 阶段6a(基础UI) ──→ 阶段6b(OCR/截图UI)
                         └──→ 阶段4(OCR) ──────↗
                                              ↓
                                         阶段7(测试+打包)
```

**M1** — 阶段 2 完成后：PyInstaller 最小可执行包验证（有 real handler 才能测真链路）  
**M3** — 阶段 2+3+5 完成后：Tauri → Python 全链路闭环（手输→保存→搜索）

---

## 阶段 1：Python 后端骨架

### 1.1 初始化工程

- 创建 `note-backend/` 目录结构
- 编写 `requirements.txt`：`pyyaml`（使用 `yaml.safe_load`，禁止 `yaml.load`，防 RCE）；`paddlepaddle` + `paddleocr` + `onnxruntime` 留到 OCR 阶段再装（ONNX 模式需 onnxruntime 做推理）
- Python 版本锁定 3.10（兼顾 PyInstaller 兼容性和 asyncio 成熟度）
- 所有文件读写显式指定 `encoding='utf-8'`
- 编写 `note-backend/logging_config.py`：RotatingFileHandler，10MB × 5 文件，输出到 `%APPDATA%/note-tray/logs/sidecar.log`，默认 INFO 级别，stderr 同步输出，30 天自动清理

**M1 触发：** 阶段 2 完成后用 PyInstaller 打包，验证 exe 可独立启动、hello 输出正常、体积可控。Tauri 集成验证留给 M3。

### 1.2 实现 JSON-RPC 行协议

- 编写 `main.py` 入口
- **I/O 架构修正：** 开独立 daemon 线程读 stdin，每读到完整一行放入 `asyncio.Queue`；事件循环从 Queue 取消息、dispatch、结果写入另一个 Queue；独立 daemon 线程从 Queue 取结果写入 stdout。避免同步 I/O 阻塞事件循环。
- 协议格式：一行一个完整 JSON，`json.loads/stdout.write` 收发
- 支持 JSON-RPC 2.0 的 request/response/error 三种消息类型
- method 路由用字典映射，未知方法返回 `-32601`
- **请求管理器：** 维护 `{requestId: asyncio.Future}` 映射表，支持 `$/cancel` 通知中断对应请求。请求完成后从映射表中移除（防内存泄漏）。`$/cancel` 仅取消 Future 等待，线程池中正在执行的任务不受影响（Python 线程不可中断），作为已知限制。
- 统一错误响应格式：`{code, message, data: {retryable, userAction, detail}}`
- 实现全部错误码定义（`-32001` ~ `-32032`、`-32600`、`-32800`）

### 1.3 实现 handshake

- Python 进程启动后第一帧主动写 stdout：`backend.hello`，包含 `version`、`protocolVersion`、`capabilities`
- **知识库目录初始化校验：** 启动时检查 `rootPath` 是否存在、是否有读写权限。异常状态通过 `system.status` 返回 `ok=false` + 对应错误码，不等写文件才报错。
- 接收 `system.shutdown` 后优雅退出
- 未捕获异常时 emit `event.fatal` 通知，再退出进程
- 编写 `system.status` handler：返回 `{ok, indexOk, ocrOk, ocrEngine, protocolVersion, rootPath, diskFree, totalNotes}`

### 1.4 实现并发模型

- 创建 IO 线程池（`concurrent.futures.ThreadPoolExecutor`，max=4）
- 创建 CPU 线程池（max=2）
- OCR 预加载使用独立后台线程，不占用 CPU 线程池
- 所有 handler 内部的文件和 CPU 操作通过 `loop.run_in_executor` 提交
- 设置超时：通用查询 5s，OCR 操作 60s，索引重建无超时（提交到 IO 线程池，非 CPU 密集）
- 编写 `system.shutdown` 的清理流程：等待线程池优雅关闭，关闭 SQLite 连接

**验收：** `python main.py` 启动，手动 echo JSON 到 stdin，收到 stdout 响应。PyInstaller 打包后在 Tauri 空壳中可启动。

---

## 阶段 2：笔记引擎

### 2.1 实现 front matter 解析

- 编写 `models/note.py`
- 解析 YAML front matter：用正则提取 `---` 包裹的头部
- 生成 front matter：`schemaVersion: 1`、自动生成的 `id`（`n_` + 8 位随机 hex）、`createdAt`、`updatedAt`（ISO 8601 + 本地时区）、`title`、`tags`、`attachments`
- 从 content 首行提取 title：匹配 `# ` 开头的行，提取 `# ` 后的完整文本作为标题
- 合并逻辑：新文件生成完整 front matter；已有文件保留原有全部字段（含用户自定义字段如 `status`, `source` 等），仅更新 `updatedAt`，`tags` 全量覆盖，`attachments` 保留

### 2.2 实现 MD 文件操作

- 编写 `storage/fs.py`
- 原子写入：`{path}.tmp.{rand8}` → `f.flush()` + `os.fsync(f.fileno())` → `os.replace(tmp, path)`。所有 open() 显式 `encoding='utf-8'`，Windows 下避免 GBK 乱码
- **路径校验升级：** 使用 `os.path.realpath()` 解析真实路径后，校验是否以知识库根目录真实路径为前缀。防范软链接/符号链接穿越，不只用字符串比较 `..` 和绝对路径。
- 文件夹自动创建：`os.makedirs(exist_ok=True)`
- 读取文件：返回原始内容和解析后的 front matter

### 2.3 实现 note.save（底层通用接口）

- 在 `main.py` 注册 handler
- **分层设计：** `note.save` 是底层通用接口，接受任意 `path` 和 `content`，直接写入指定文件。按天追加、时间戳格式化等逻辑封装在上层业务接口（`ocr.store`、`clipboard.ingest`）中，不放在 note.save 内部。
- 参数：`{path, content, tags?}` — 返回 `{noteId, savedAt, path}`
- 新文件 → 生成 front matter + content → 原子写入
- 已有文件 → 读取原内容 + front matter → 更新 `updatedAt`/`tags` → 完全替换原内容（不追加）→ 原子写回
- 文件无 front matter → 自动为其添加标准 front matter（schemaVersion + id + createdAt + updatedAt）

### 2.4 实现上层业务接口

- `note.get`：参数 `path`，返回 `{noteId, path, content, frontMatter}`
- **note.delete（修正）：** 删除前先解析 front matter 获取所有 `attachments[].path`，逐一删除图片文件（文件不存在时打 WARN 日志，不中断流程），再删除 .md 文件
- `note.move`：参数 `from`、`to`，`os.rename` + 边界检查；目标已存在时返回错误，不覆盖；FTS5 同步更新 `doc_path`
- `clipboard.ingest` / `clipboard.ingest_image`：`text` 为空或图片无效 → 返回 `PARSE_ERROR`，不写空文件
- `note.list_folder`：参数 `folder`，遍历目录返回所有 .md 文件的 `{path, title, updatedAt, snippet}` 列表。`snippet` 取 .md 文件前 100 字符，正则剥离常见 Markdown 符号（# ` * []() > -）后返回纯文本，不做 AST 级解析
- `browser.tree`：参数 `root`（可选），递归遍历生成 `{tree: [{name, path, type, children?}]}`
- `attachment.list`：参数 `noteId`，解析 front matter 返回 `{attachments: [...]}`
- `clipboard.ingest`：参数 `{text, folder?, tags?}` — 上层业务逻辑：确定目标路径 `{folder}/note-{date}.md`，文件不存在→调 `note.save` 新建，已存在→读取原内容→追加 `\n\n---\n\n## {HH:MM}\n\n{text}` → 调 `note.save` 写回；`tags` 透传到 `note.save`
- `clipboard.ingest_image`：参数 `{imagePath, folder?, mode}` — 内部调用 `ocr.store`（OCR 阶段实现后补上）

**验收：** 通过 stdin 逐一调用各方法；`clipboard.ingest` 验证按天追加 + 时间戳格式正确。

---

## 阶段 3：搜索

### 3.1 实现 FTS5 索引管理

- 编写 `search.py`
- **单连接 + 全局锁：** 使用单例 `sqlite3.connect("%APPDATA%/note-tray/note.db", check_same_thread=False)` + `threading.Lock`，所有读写串行化。MVP 量级下完全够用，避免多线程并发写锁竞争。
- 初始化：连接到 `%APPDATA%/note-tray/note.db`，开启 WAL 模式
- 建表：`CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(doc_path UNINDEXED, title, content, ocr_text, tokenize='trigram')`
- 写入：`DELETE` + `INSERT`，包裹在 `BEGIN/COMMIT` 事务中
- 查询：FTS5 MATCH + `snippet()` + `bm25()`，带 `offset` 分页；查询后从 front matter 补入 `updatedAt`
- **查询转义：** 用户输入中的 `*` `"` `+` `-` `AND` `OR` `NOT` 等 FTS5 操作符，将整个查询用双引号 `"` 包裹转义为短语搜索，禁用语法解析（不损失 trigram 匹配能力）
- **启动增量同步：** 初始化时全量扫描知识库 .md 文件，对比文件 `mtime` 与 FTS5 中记录的最后更新时间，补齐外部编辑器产生的差异。避免外部改的文件搜不到。
- 中文搜索已知问题记录：trigram 对 2 字词、单字词的召回偏弱（如搜"线程"可命中但精度不高），作为 MVP 已知问题。
- **FTS5 定期 merge：** 每 1000 次写入执行 `INSERT INTO notes_fts(notes_fts) VALUES('merge')`，防止索引层数过深退化

### 3.2 集成 note.save 与搜索

- `note.save` 和 `clipboard.ingest` 在原子写 .md 成功后，同步更新 FTS5 表
- `note.delete` 后同步删除 FTS5 对应行
- 编写 `search.query` handler：调用 FTS5 查询，格式化返回
- **重建索引异步化：** `search.rebuild` 提交到 IO 线程池后台执行（非 CPU 密集），不阻塞 RPC 调用。完成后推送 `event.index.rebuilt` 事件通知前端。`$/cancel` 仅取消等待中的 Future，已在执行的遍历操作无法中断，作为已知限制。
- `system.status` 中 `indexOk` 根据 FTS5 连接是否存在来判断

**验收：** 保存后立即搜索命中；外部编辑器改文件后搜索反映最新内容；重建索引不阻塞其他操作。

---

## 阶段 4：OCR 引擎

### 4.1 实现 PaddleOCR 封装

- 安装依赖：`paddlepaddle`、`paddleocr`、`onnxruntime`（ONNX 推理引擎）
- 编写 `ocr/engine.py`
- **加载时机修正：** 进程启动后后台异步预加载 PaddleOCR（独立后台线程，不占用 CPU 线程池），不阻塞握手和 hello。用户首次使用时大概率已加载完成。
- **首次下载处理：** PaddleOCR 首次运行时自动下载 ~15MB 模型到 `~/.paddleocr/`。下载期间 preload 线程阻塞等待；失败则标记 `ocrEngine="unavailable"`，下次启动重试。无进度反馈，作为已知限制。
- **置信度阈值：** `extract()` 返回后，置信度 < 0.5 的结果丢弃，不写入笔记正文（附件 ocrText 中保留原始输出）。
- 加载中 `ocrEngine="loading"`，完成 `ocrEngine="paddleocr"`，失败 `ocrEngine="unavailable"`
- 核心方法 `extract(image_path: str) -> dict`：返回 `{text, confidence, engine, timeMs}`

### 4.2 实现 OCR handler

- **Windows OCR fallback 延期：** MVP 只保证 PaddleOCR 可用，Windows OCR 兼容性问题多，放到后续迭代。
- 编写 `ocr.extract` handler：接收 image_path，调用引擎提取文字
- 编写 `ocr.store` handler（上层业务逻辑）：
  - 接收 imagePath + folder + mode
  - 确定目标笔记路径：`{folder}/note-{date}.md`
  - 复制图片到 `{folder}/images/` 目录，命名 `{date}-{8位随机hex}.png`；复制失败（权限/磁盘满/路径超长）→ 返回 `DISK_FULL` 或对应错误，不继续 OCR 流程
  - 执行 OCR 获取文字
  - mode="text"：调 `clipboard.ingest` 将 OCR 文字追加到当日笔记（仅提取文字，图片不保存为附件）
  - mode="image"：追加附件引用（无 ocrText）到笔记 front matter → 调 `note.save` 写回文件（新增附件追加而非覆盖已有）
  - mode="both"：保存图片到附件（含 ocrText）→ 更新 FTS5 `ocr_text` → 同时调 `clipboard.ingest` 将 OCR 文字追加到当日笔记正文
  - **OCR 文字聚合：** 一篇笔记多个附件时，FTS5 的 `ocr_text` 字段是所有附件 OCR 文字的聚合，新增时追加而非覆盖。

### 4.3 集成 OCR 状态

- `ocrOk` 和 `ocrEngine` 由 engine 模块内部状态决定
- `event.ocr_ready`：引擎加载完成时推送
- system.status handler 实时读取引擎状态

**验收：** 启动后几秒内 OCR 就绪（后台加载）；截图→提取文字→保存流程完整。

---

## 阶段 5：Tauri 桌面壳

### 5.1 初始化 Tauri 工程

- `npm create tauri-app@latest note-tray`，选 React + TypeScript 模板
- 配置 sidecar：在 `tauri.conf.json` 中声明 Python 进程为 sidecar（开发期指向 `python main.py`，打包期指向 PyInstaller 产物）
- 编写 `main.rs`：入口，初始化各模块，启动系统托盘和热键

### 5.2 实现系统托盘

- 编写 `tray.rs`
- 托盘菜单项：快速笔记、截图笔记、打开知识库、搜索、设置、日志、退出
- 状态图标：正常=绿色，Python 不可用=黄色，5 次崩溃=红色
- 点击事件：正常状态→显示菜单，红色状态→弹出「后端异常，点击重试」对话框

### 5.3 实现全局热键

- 编写 `hotkey.rs`
- 默认 `Ctrl+Shift+N`，预设 3 组
- 热键触发 → 通知前端弹出捕获窗口
- 支持用户在设置页自定义

### 5.4 实现 Python 进程管理

- 编写 `sidecar.rs`
- 启动：`Command::new("python/main.exe")` spawn，设置环境变量 `NOTE_KB_ROOT`
- **单实例锁：** 启动时创建/检查知识库目录下的 `.note.lock` 文件锁（`msvcrt.locking` 或 `fcntl.flock`），防止多进程同时操作同一知识库导致文件相互覆盖
- 握手：等待 stdout 第一帧 `backend.hello`，匹配 `protocolVersion`
- 不兼容 → 弹出升级提示并 `backend.shutdown`
- 退避重启：1s→5s→15s→60s→放弃（5 次后不再自动重试）
- 健康检查：每 30s 调用 `system.status` 检查 `ok` 字段
- 退出：发送 `system.shutdown`，等待进程退出
- **管道防死锁：** Tauri 必须异步读取 stdout 和 stderr，stderr 管道满会阻塞整个 Python 进程

### 5.5 实现 JSON-RPC 客户端

- 编写 `rpc.rs`
- 从 stdout 逐行读 JSON，解析 response/event
- 发送请求：构造 JSON-RPC 请求写入 stdin
- 请求-响应匹配：按 `id` 字段关联
- **超时与重试：** 查询请求 5s 超时，OCR 60s。仅当 error.data.retryable=true 时自动重试 1 次，仍失败才抛出给用户；retryable=false 的错误直接抛出。
- 事件处理：`event.ocr_ready` / `event.fatal` / `event.index.rebuilt` 通过 channel 通知前端

### 5.6 实现截图选区

- 编写 `screenshot.rs`
- **MVP 方案：** 直接复用 Windows 系统截图（`Win+Shift+S`）。轮询剪贴板检测新图片（每 200ms，超时 10s），拿到结果后保存到 `%APPDATA%/note-tray/tmp/`。自绘选区放后续迭代。
- **临时文件清理：** 正常路径：单次操作完成（保存成功 / 用户取消）后立即删除 tmp 目录下的临时图片。兜底路径：启动时清理超过 24 小时的残留 tmp 文件。

### 5.7 实现剪贴板读取

- 编写 `clipboard.rs`
- 检测剪贴板内容类型：文本 / 图片 / 空
- 文本 → 传给前端，走 `clipboard.ingest` RPC
- 图片 → 保存到 `%APPDATA%/note-tray/tmp/` → 通知前端，走 `clipboard.ingest_image` RPC
- 临时文件清理与 §5.6 策略一致：操作完成即删 + 启动时清超过 24h 残留

**验收：** Tauri 打包运行，托盘图标出现，Ctrl+Shift+N 弹出窗口，Python 进程成功启动并握手。

---

## 阶段 6：React 捕获窗口

### 6.1 实现捕获窗口框架

- 编写 `CaptureWindow.tsx`
- 400×300 固定尺寸，屏幕居中，窗口置顶
- 三模式 Tab 切换：[截图] [手输] [剪贴板]
- 通过 Tauri IPC 调用 Rust 层（Rust 再通过 RPC 转发业务操作到 Python）
- 加载态：Python 未握手时显示「启动中…」，5 秒超时提示重试

### 6.2 实现文件夹选择器

- 下拉列表显示已有文件夹（调用 `browser.tree`），按最近使用排序
- 支持输入新文件夹名自动创建
- 选中文件夹后，侧边显示 `note.list_folder` 返回的最近 5 篇笔记标题和时间
- 本地缓存最近使用的文件夹列表

### 6.3a 实现基础 UI 联调（阶段 2+5 完成后立即开始）

- **手输模式：** 轻量 Markdown 编辑区（`textarea` + 基础语法高亮即可，不引入重型编辑器，控制包体积），Ctrl+V 粘贴图片走 OCR 流程
- **搜索窗口：** 输入框 + 结果列表（标题、路径、高亮片段、时间），Enter 打开文件，Ctrl+C 复制路径。输入需做 300ms 防抖，避免每次按键都发 RPC
- **设置页：** 知识库根目录选择、热键自定义、「重建索引」按钮、「查看日志」按钮

### 6.3b 实现截图+OCR UI（阶段 4 完成后接续）

- **截图模式：** 调用系统截图 → 显示缩略图 → 可选 [提取文字] / [保存附件] / [两者]
- OCR 按钮状态：`ocrEngine="loading"`→ 提示"初始化 OCR…"；`="paddleocr"`→ 可用；`="unavailable"`→ 置灰
- **剪贴板模式：** 自动识别剪贴板内容 → 图片走 `ocr.store`，文本走 `clipboard.ingest`

**验收：** 完整交互流程：热键 → 选文件夹 → 输入文本 → 保存 → 搜索 → 打开笔记；截图 → 提取文字 → 保存。

---

## 阶段 7：测试与打包

### 7.1 Python 单元测试

- 编写 pytest 测试：front matter 解析、原子写入、FTS5 查询、OCR 封装、错误码覆盖
- 使用 `tmp_path` 隔离文件系统操作
- 覆盖正常场景：新建/追加/删除/重建索引/路径校验
- **覆盖异常场景：** 文件锁定、内容超限、路径穿越防护、知识库目录丢失、磁盘空间不足

### 7.2 集成测试

- 编写集成测试脚本：启动 Python 进程 → 通过 stdin 发送 JSON-RPC 请求 → 验证 stdout 响应
- 覆盖全量方法
- 补充：Python 崩溃重启验证、索引损坏重建验证

### 7.3 打包与交付

- Tauri 配置打包为 MSI/NSIS 安装包
- **PyInstaller 前置验证：** 阶段 2 完成后就用 PyInstaller（`--onedir` 模式，启动快）打包最小 Python 后端，放到 Tauri 里启动，验证拉起→通信→体积。不等到阶段 7。
- **体积预期修正：** 目标 `< 80MB`。Tauri 约 5MB + Python 基础环境约 15-20MB + PaddleOCR 约 15-50MB → 整体约 60-80MB。如体积超标，考虑 OCR 模型按需下载方案。

### 7.4 文档

- 编写 `README.md`：安装步骤、使用说明、目录结构说明
- 更新设计文档状态为「已实现」
- 记录已知问题：trigram 中文 2 字词召回弱；Windows OCR 暂未实现；`$/cancel` 仅取消 Future 等待，线程池任务不可中断；`clipboard.ingest`/`ocr.store` 的文件操作与 FTS5 非原子；OCR 首次模型下载无进度反馈

---

## 里程碑详情

| 里程碑 | 触发时机 | 验证内容 |
|--------|---------|---------|
| **M1** | 阶段 2 完成 | PyInstaller 打出的 exe 在 Tauri 空壳中启动成功，体积可控 |
| **M3** | 阶段 2+3+5 完成 | Tauri 启动 Python → 手输文本 → 保存为 .md → 搜索命中 |

M3 完成后即可并行推进：
- 阶段 6a（基础 UI 联调）
- 阶段 4（OCR 引擎开发）

---

(以下为 spec v2 已同步的变更，均已落实在 spec 中，不再单独追踪)
