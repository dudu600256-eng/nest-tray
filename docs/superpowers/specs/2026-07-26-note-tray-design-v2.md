# Note Tray — 个性化学习闭环工具 设计文档 v2

> 版本: 2.0 (减重版)  
> 日期: 2026-07-26  
> 状态: Draft

## 1. 产品概述

### 1.1 解决的问题

学习/开发/阅读过程中遇到的知识点——当时没记录，事后忘了；记了也零散、不成体系。

### 1.2 核心价值

- **极低摩擦**：全局热键 → 弹窗 → 记录 → 消失，不打断工作流
- **数据自有**：纯 Markdown 文件 + 文件夹，离开工具也能用，网盘即同步
- **真正轻量**：安装包 < 80MB，稳态内存 < 250MB

### 1.3 使用场景

| 场景 | 操作 | 结果 |
|------|------|------|
| 代码报错 | Ctrl+Shift+N → 截图 → 提取文字 → 选 SpringAI 文件夹 | 自动追加入 `SpringAI/note-2026-07-26.md` |
| 看到好文章段落 | Ctrl+Shift+N → 截图区域 | 图片存附件 + OCR 文字可搜索 |
| 突然想到一个点 | Ctrl+Shift+N → 手输 → 选文件夹 | Markdown 内容追加到当天笔记 |
| 搜索之前记的 | 托盘 → 搜索 | 全文搜索结果 + 上下文片段 |

### 1.4 范围

**一期：**
- 系统托盘 + 全局热键 + 捕获窗口 (截图/手输/剪贴板)
- 纯 MD 文件存储 + 文件夹组织
- OCR (截图→文字 + 截图存附件)
- 全文搜索 (SQLite FTS5)
- 日志

**二期 (独立模块，不影响一期)：**
- 知识图谱 (`[[笔记名]]` 引用 + 标签共现)
- 网盘同步 (一期天然支持——纯文件)

**明确不做：** 移动端、多人协作、云服务、账号系统。

---

## 2. 技术架构

### 2.1 选型

| 层 | 技术 | 理由 |
|----|------|------|
| 壳 | Tauri v2 (Rust) | 轻，管理系统托盘/热键/窗口 |
| UI | React + TypeScript | 截图 UI 好写，AI 训练数据丰富 |
| 后端 | Python (sidecar 进程) | OCR 生态最强，AI 辅助开发友好 |
| 通信 | JSON-RPC 2.0 行协议 (stdin/stdout) | 一行一个 JSON，无网络暴露 |
| 存储 | 纯文件系统 (.md) | 数据自有，工具无关 |
| OCR | PaddleOCR (onnx) | 中文最优，MVP 唯一引擎 |
| 搜索 | SQLite FTS5 | 三行建表，自带 BM25，原子性免费 |

### 2.2 整体分层

```
┌─────────────────────────────────────────┐
│  Tauri (Rust)                           │
│  托盘、热键、截图选区、窗口、sidecar 管理  │
├─────────────────────────────────────────┤
│  Python (sidecar, stdin/stdout)         │
│  OCR、MD读写、FTS5搜索、附件管理         │
├─────────────────────────────────────────┤
│  文件系统                               │
│  {知识库根目录}/                         │
│    SpringAI/                            │
│      note-2026-07-26.md             │
│      note-2026-07-27.md             │
│      images/2026-07-26-a1b2.png         │
│  %APPDATA%/note-tray/                  │
│    note.db        ← SQLite (FTS5索引)    │
│    tmp/           ← 临时文件 (启动清空)   │
│    logs/          ← 日志                │
└─────────────────────────────────────────┘
```

### 2.3 边界约束

- `.md` 是唯一数据源，`note.db` 可随时删除重建
- Rust 不含业务逻辑，所有函数是调用 Python 的薄壳
- Python 不负责 UI
- 二期模块独立，零侵入
- **所有文件操作使用 `encoding='utf-8'`**，不依赖系统默认编码（Windows 系统编码可能为 GBK）
- **YAML 解析使用 `yaml.safe_load()`**，禁止 `yaml.load()`（防止通过 YAML 标签执行任意代码）
- **SQLite 连接设置 `check_same_thread=False`**，配合 `threading.Lock` 实现跨线程串行访问
- **stdout/stderr 均使用 Python `print(..., flush=True)`**，防止管道缓冲满导致的死锁
- **Python 版本锁定为 3.10**，兼顾 PyInstaller 兼容性和 asyncio 特性

### 2.4 配置管理

配置文件由 Tauri 管理，路径：`%APPDATA%/note-tray/config.json`（Windows 标准应用数据目录），不存入 `%APPDATA%/note-tray/`（该目录仅由 Python 管理）。

```json
{
  "kbRoot": "D:/notes",
  "hotkey": "Ctrl+Shift+N",
  "lastFolder": "SpringAI"
}
```

- Tauri 启动时读取 `config.json`，通过环境变量 `NOTE_KB_ROOT` 传给 Python sidecar
- 用户在设置页修改后，Tauri 写回 `config.json`。Python 不直接读写 `config.json`，通过 `system.status` 每次请求时从环境变量重读 `NOTE_KB_ROOT`
- `lastFolder` 由 Tauri 在每次 `note.save`/`ocr.store`/`clipboard.ingest` 成功后更新，重启后恢复使用

---

## 3. 通信协议

### 3.1 行协议

```
stdin/stdout，一行一个完整的 JSON，json.dumps 自动处理换行转义:

{"jsonrpc":"2.0","id":"r1","method":"note.save","params":{"path":"SpringAI/note-2026-07-26.md","content":"# 线程池报错\n\n配置线程池时发现…"}}

通道分工: stdin=请求, stdout=响应+事件, stderr=日志
```

### 3.2 Handshake

Python 进程启动后主动发第一帧：

```json
{"jsonrpc":"2.0","method":"backend.hello","params":{"version":"0.1.0","protocolVersion":1,"capabilities":["note","search","ocr"]}}
```

Tauri 匹配 `protocolVersion`，不兼容则发 `backend.shutdown` 并提示升级。`capabilities` 控制前端功能入口显隐。

### 3.3 方法列表

| 分类 | 方法 | 参数 | 返回 |
|------|------|------|------|
| 笔记 | `note.save` | `{path, content, tags?}` | `{noteId, savedAt, path}` |
| 笔记 | `note.get` | `{path}` | `{noteId, path, content, frontMatter}` |
| 笔记 | `note.delete` | `{path}` | `{}` |
| 笔记 | `note.move` | `{from, to}` | `{}` |
| 笔记 | `note.list_folder` | `{folder}` | `{notes: [{path, title, updatedAt, snippet}]}` |
| 浏览 | `browser.tree` | `{root?}` | `{tree: [{name, path, type, children?}]}` |
| 搜索 | `search.query` | `{q, limit?, offset?}` | `{results: [{path, title, snippet, score, updatedAt}], totalHits, timeMs}` |
| 搜索 | `search.rebuild` | `{}` | `{}` |
| OCR | `ocr.extract` | `{imagePath}` | `{text, confidence, engine, timeMs}` |
| OCR | `ocr.store` | `{imagePath, folder?, mode}` | `{noteId, ocrText}` |
| 附件 | `attachment.list` | `{noteId}` | `{attachments: [...]}` |
| 系统 | `system.status` | `{}` | `{ok, indexOk, ocrOk, ocrEngine, protocolVersion, rootPath, diskFree, totalNotes}` |
| 系统 | `system.shutdown` | `{}` | `{}` |
| 剪贴板 | `clipboard.ingest` | `{text, folder?, tags?}` | `{noteId}` |
| 剪贴板 | `clipboard.ingest_image` | `{imagePath, folder?, mode}` | `{noteId, ocrText}` |

**默认值规则：** `folder` 不传时，默认使用最近一次成功保存的文件夹（持久化于 `config.json` 的 `lastFolder` 字段）；无最近记录则保存到知识库根目录。

**note.save 参数说明：**
- `path` — 知识库内的完整相对路径，如 `"SpringAI/note-2026-07-26.md"`；路径校验使用 `os.path.realpath()` 防符号链接穿越
- `content` — Markdown 正文（不含 front matter）
- `tags?` — 可选，覆盖式更新
- 新文件 → 生成完整 front matter + content → 原子写入
- 已有文件 → 读取原内容 + front matter → 保留原 front matter 全部字段（含用户自定义），仅更新 updatedAt/tags → 完全替换原内容 → 原子写回
- 文件无 front matter → 自动添加标准 front matter（schemaVersion + id + createdAt + updatedAt），不报错

**上层业务接口（按天追加逻辑封装于此，不放在 note.save 内部）：**
- `clipboard.ingest`：确定目标路径 `{folder}/note-{date}.md`，文件不存在→调 note.save 新建，已存在→读取原内容→追加 `\n\n---\n\n## {HH:MM}\n\n{text}` → 调 note.save 写回
- `ocr.store`：确定目标路径 `{folder}/note-{date}.md`，`imagePath` 为已落盘的临时图片绝对路径，复制图片到 `{folder}/images/`，执行 OCR，按 mode 写入
- `clipboard.ingest_image`：`imagePath` 为已落盘的临时图片绝对路径，内部调用 `ocr.store`。作为独立 RPC 方法是为了前端统一入口——前端无需区分截图还是剪贴板，直接调此方法即可。

**ocr.store 的 mode 枚举：**
- `"text"` — 仅提取文字插入笔记正文
- `"image"` — 仅保存图片到附件目录，不提取文字
- `"both"` — 两者都做 (默认)

**note.list_folder 的 snippet 定义：** 正文前 100 字符的纯文本摘要（不含 Markdown 格式符号；去除 Markdown 符号使用正则剥离实现，不做 AST 级解析，成本可控）。

**attachment.list 调用模式：** 该 API 通过 `noteId` 查询附件。用户从搜索/浏览只拿到文件 `path` 时，需先调 `note.get(path)` 获取 `noteId`，再调 `attachment.list(noteId)`。

**browser.tree 限制：** 递归深度限制 3 层，超出的文件夹折叠不展开。

**图片大小校验：** `ocr.store` 和 `ocr.extract` 传入图片超过 20MB → 返回 `-32023 OCR_IMAGE_TOO_LARGE`，不执行 OCR。

**note.move 边界规则：** 目标路径已存在时返回 `NOTE_MOVE_DEST_EXISTS`，不覆盖。`from` 不存在返回 `NOTE_NOT_FOUND`。目标路径与 `from` 相同视为成功（无操作）。

**clipboard.ingest 空输入：** `text` 为空字符串 → 返回 `PARSE_ERROR`，不写空文件。

**browser.tree 排序：** 同一层级按文件/文件夹名拼音序排列。`note.list_folder` 按 mtime 降序排列。

**跨层调用错误传播：** `ocr.store` 内部调 `clipboard.ingest`，`clipboard.ingest` 内部调 `note.save`——均为同进程直接函数调用（非 RPC 嵌套），以 Python 异常透传。内层异常（如 NOTE_LOCKED）被外层 catch 后，外层方法返回对应的 RPC 错误码，不做二次封装。所有内部调用的异常均被顶层 handler catch 并转为统一 RPC 错误响应。

### 3.4 Cancel

```
Tauri:  {"jsonrpc":"2.0","method":"$/cancel","params":{"requestId":"r42"}}
Python: {"jsonrpc":"2.0","id":"r42","error":{"code":-32800,"message":"cancelled"}}
```

### 3.5 事件 (Python → Tauri)

| 事件 | 触发时机 |
|------|---------|
| `event.ocr_ready` | OCR 引擎首次加载完成，参数 `{ocrEngine, ready}` |
| `event.index.rebuilt` | 索引重建完成，参数 `{totalFiles, timeMs}` |
| `event.fatal` | Python 进程即将异常退出 |

---

## 4. 错误模型

### 4.1 结构

```json
{
  "code": -32001,
  "message": "笔记不存在",
  "data": {
    "retryable": false,
    "userAction": "检查文件是否已被移动或删除",
    "detail": {}
  }
}
```

### 4.2 错误码表

| code | 名称 | retryable | userAction |
|------|------|-----------|------------|
| -32001 | NOTE_NOT_FOUND | 否 | 检查文件是否被删除或移动 |
| -32002 | NOTE_PATH_INVALID | 否 | 路径不能包含 `..` 或指向知识库外 |
| -32003 | NOTE_CONTENT_TOO_LARGE | 否 | 单篇笔记限制 1MB，请拆分 |
| -32004 | NOTE_LOCKED | 是 | 文件被占用，稍后重试 |
| -32005 | NOTE_MOVE_DEST_EXISTS | 否 | 目标路径已有文件，不能覆盖 |
| -32011 | SEARCH_ERROR | 否 | 搜索索引异常，可尝试重建 |
| -32021 | OCR_NOT_READY | 是 | OCR 引擎加载中，稍后重试 |
| -32022 | OCR_UNSUPPORTED_FORMAT | 否 | 不支持该图片格式，请转 PNG/JPG |
| -32023 | OCR_IMAGE_TOO_LARGE | 否 | 图片超过 20MB，请先压缩 |
| -32024 | OCR_FAILED | 是 | OCR 识别失败，可重试 |
| -32031 | DISK_FULL | 否 | 磁盘空间不足 |
| -32032 | KB_PATH_NOT_FOUND | 否 | 知识库目录不存在，请在设置中指定 |
| -32800 | CANCELLED | 是 | 操作已取消 |
| -32600 | PARSE_ERROR | 否 | RPC 解析失败 |

---

## 5. 数据模型

### 5.1 Note 结构

```markdown
---
schemaVersion: 1
id: n_abc123
title: Spring AI 多线程配置问题
createdAt: 2026-07-26T12:00:00+08:00
updatedAt: 2026-07-26T18:45:00+08:00
tags: [SpringAI, 多线程]
attachments:
  - hash: a1b2c3d4
    path: images/2026-07-26-a1b2.png
    ocrText: "Exception in thread \"main\" java.lang.NullPointerException"
---

# Spring AI 多线程配置问题

## 14:30

配置 Spring AI 的线程池时发现…

## 18:45

后来查到是因为…

---

## 19:00

还有个问题…
```

> 示例：`SpringAI/note-2026-07-26.md`，一天内多次捕获追加到同一文件，`---` 分割线隔开不同记录。追加时自动添加 `## HH:MM` 时间戳标题作为记录分隔。

### 5.2 字段

| 字段 | 必须 | 说明 |
|------|------|------|
| `schemaVersion` | 是 | 当前为 1，预留未来升级 |
| `id` | 否 | 自动生成 |
| `title` | 否 | 从 content 首行 `# ` 提取，自动填充；若 content 首行无 `# ` 标题，则保留 front matter 中已有的 `title` |
| `createdAt` | 否 | ISO 8601 |
| `updatedAt` | 否 | 每次保存自动更新 |
| `tags` | 否 | 用户自定义 |
| `links` | 否 | 二期预留，一期不写入（避免空数组噪音） |
| `attachments` | 否 | 图片附件 (hash, path, ocrText) |

**路径约定：** `attachments[].path` 和 `links[].target` 均相对于知识库根目录。移动笔记文件时无需修改这些引用。

### 5.3 版本迁移

```
MVP 只有一个版本 v1，迁移代码量为零。
schemaVersion 字段预占，未来需要时再加迁移逻辑。
```

---

## 6. 系统状态

```
system.status 返回:

{
  "ok": true,                        // 后端整体可用
  "indexOk": true,                   // FTS5 索引可用
  "ocrOk": false,                    // OCR 引擎可用（后台预加载）
  "ocrEngine": "loading",           // "loading" | "paddleocr" | "unavailable"
  "protocolVersion": 1,              // 当前 RPC 协议版本
  "rootPath": "D:/notes",
  "diskFreeBytes": 53687091200,
  "totalNotes": 1542
}
```

三个 bool 替代五态状态机。前端根据它们决定 UI 控件的启用/禁用状态即可。

**totalNotes 计算：** 启动时统计一次 .md 文件数量，后续由 note.save/delete 增减计数，不做全量扫描。每 30 分钟全量扫描矫正一次，防止外部操作导致的计数漂移。

### Python 进程管理

```
Tauri 启动 → spawn Python → hello 握手 → ok=true
Python 崩溃 → 自动重启 (退避: 1s→5s→15s→60s→放弃, 托盘变红)
超过 5 次失败 → 不再重启，托盘变红；用户点击托盘图标弹出「Python 后端异常，点击重试」，用户确认后重新 spawn
```

**Tauri 轮询频率：**
- 稳态：每 30s 轮询一次 `system.status`，仅做健康检查
- `event.ocr_ready` 事件主动推送，Tauri 收到后更新 UI

---

## 7. 捕获窗口 (UX)

### 7.1 触发

```
Ctrl+Shift+N (可自定义，预设 3 组)
  → T+50ms: 窗口弹出 (400×300, 居中, 置顶)
            笔记保存/截图立即可用；搜索需等 FTS5 启动同步完成；OCR 提取需等引擎预加载就绪
  → T+200ms: hello 握手完成，ok=true
  → 未就绪时 Tauri 暂存用户输入到内存
  → 超过 5 秒未握手 → 提示「后端启动超时，是否重试?」
```

**首次启动引导：** 弹出设置窗口，选择或创建知识库根目录。可选创建示例笔记。

### 7.2 三种模式

**截图模式 (默认)：**
1. 点截图 → 调用 Windows 系统截图（`Win+Shift+S`）→ 选区完成
2. 截图结果自动传入剪贴板，工具轮询剪贴板检测新图片（每 200ms，最多等 10s）；若剪贴板已有图片则无法区分新旧，用户需手动清除旧剪贴板
3. Esc 或右键取消截图
4. 支持多屏
5. 选择：[提取文字] / [保存附件] / [两者]
6. 选目标文件夹 → Ctrl+Enter 保存

**文件夹选择器：** 下拉列表显示已有文件夹（按最近使用排序），输入新名称则自动创建。选中文件夹后侧边显示该文件夹下最近 5 篇笔记的标题和时间，提供上下文关联感。

**手动输入：** Markdown 编辑区 + 语法高亮，Ctrl+V 粘贴图片走 OCR，支持拖入。

**剪贴板：** 自动识别图片/文本，图片走 `ocr.store`，文本走 `clipboard.ingest`。

### 7.3 剪贴板链路

```
截图 ──→ 图片 ──→ Python ocr.store() ──→ .md + 附件
剪贴板图片 ──→ tmp/ → Python clipboard.ingest_image() ──→ ocr.store() ──→ .md + 附件
手输/KB文本 ──→ Python clipboard.ingest() ──→ note.save() ──→ .md
```

临时文件写入 `%APPDATA%/note-tray/tmp/`，每次启动清空超过 24 小时的残留文件 (防止进程崩溃导致的临时文件堆积)。

### 7.4 托盘菜单

```
📝 快速笔记 | 📷 截图笔记
📂 打开知识库 | 🔍 搜索
⚙ 设置 | 📊 日志
⏸ 退出
```

### 7.5 搜索窗口

独立窗口，输入关键词 → 显示匹配笔记路径 + 高亮上下文片段（前端渲染 `<mark>` 标签需用 `dangerouslySetInnerHTML` 或自定义渲染组件，防止被转义为纯文本）。Enter 用系统编辑器打开，Ctrl+C 复制路径。

**输入防抖：** 搜索输入需做 300ms 防抖，避免每次按键都触发 RPC 调用。

---

## 8. OCR 引擎

### 8.1 选择

PaddleOCR (onnx) 作为 MVP 唯一引擎，成功则启用，失败则禁用 OCR 功能。ONNX 推理需额外安装 `onnxruntime`。

**首次模型下载：** PaddleOCR 首次运行时自动下载 ~15MB 模型到 `~/.paddleocr/`。下载期间 preload 线程阻塞等待，下载完成后自动加载。如下载失败（网络不可用等），preload 线程标记 `ocrEngine="unavailable"`，下次启动重试。

**OCR 置信度阈值：** 置信度 < 0.5 的识别结果不写入笔记正文，仅在 front matter attachments 的 ocrText 中记录原始输出，由人工判断。

### 8.2 生命周期

```
进程启动 → hello (ocr 不阻塞，后台异步预加载 PaddleOCR)
 → ocrEngine="loading"，ocrOk=false，截图/保存/搜索立即可用
 → 预加载成功 → ocrEngine="paddleocr"，ocrOk=true，event.ocr_ready 推送
 → 预加载失败 → ocrEngine="unavailable"，ocrOk=false，OCR 按钮置灰
 → 用户首次调用时大概率已加载完成
```

**event.ocr_ready 参数：**

```json
{"jsonrpc":"2.0","method":"event.ocr_ready","params":{"ocrEngine":"paddleocr","ready":true}}
```

### 8.3 输出

```json
{"text": "Exception in thread...", "confidence": 0.93, "engine": "paddleocr", "timeMs": 320}
```

---

## 9. 搜索 (SQLite FTS5)

### 9.1 建表

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    doc_path UNINDEXED,  -- 不索引，仅作为笔记路径标识
    title,               -- 标题
    content,             -- 正文
    ocr_text,            -- 附件 OCR 文字 (聚合)
    tokenize='trigram'
);
```

**分词策略：** `trigram` tokenizer 同时处理中英文。英文字母自然成词，中文按 3-gram 滑动窗口切分。"线程池"被切为 `线,程,池,线程,程池,线程池`——搜"线程"能命中"线程池"，搜"NullPointerException"能精确匹配。零额外依赖。

### 9.2 写入

```
note.save (底层):
  1. 校验路径 (os.path.realpath)
  2. 写入 .md 文件: 新文件→生成 front matter+content，已有文件→更新 front matter+替换 content
  3. 原子写入: tmp → fsync → os.replace
  4. BEGIN TRANSACTION;
     DELETE FROM notes_fts WHERE doc_path = ?;
     INSERT INTO notes_fts (doc_path, title, content, ocr_text) VALUES (?, ?, ?, ?);
     COMMIT;
  
  说明：`note.save` 本身不处理图片，`ocr_text` 取值为现有 front matter 中 attachments 的 ocrText 聚合（空则空串）。ocr_text 的增量更新由 `ocr.store` 触发。
   
clipboard.ingest (上层业务):
  1. 确定目标路径: {folder}/note-{date}.md
  2. 文件不存在→调 note.save 新建
  3. 文件已存在→读取原内容→追加 `\n\n---\n\n## {HH:MM}\n\n{text}` → 调 note.save 写回
  4. ⚠ 并发竞态：同文件夹同时写入互相覆盖。规避方案：note.save 内部对目标路径加 threading.Lock 文件级锁

note.move (同步 FTS5):
  1. os.rename + 路径校验
  2. UPDATE notes_fts SET doc_path = ? WHERE doc_path = ?

note.delete (同步 FTS5):
  1. 删除 .md 及附件图片
  2. DELETE FROM notes_fts WHERE doc_path = ?
   
ocr.store (上层业务):
  1. 确定目标路径: {folder}/note-{date}.md
  2. 复制图片到 {folder}/images/ 目录
  3. 执行 OCR，获取文字
  4. mode="text"：调 clipboard.ingest 将 OCR 文字追加到当日笔记（仅提取文字，图片不保存为附件）
  5. mode="image"：仅追加附件引用到笔记 front matter → 调 note.save 写回（图片路径存入 front matter，不提取文字，FTS5 ocr_text 不变）
  6. mode="both"：保存图片到附件（含 ocrText）→ 更新 FTS5 ocr_text → 同时调 clipboard.ingest 将 OCR 文字追加到当日笔记正文
```

### 9.3 查询

```sql
SELECT doc_path, title, snippet(notes_fts, 2, '<mark>', '</mark>', '...', 40) AS snippet, bm25(notes_fts) AS score
FROM notes_fts
WHERE notes_fts MATCH ?
ORDER BY bm25(notes_fts)
LIMIT 20;
```

**查询转义：** 用户输入中的 FTS5 操作符（`*` `"` `+` `-` `AND` `OR` `NOT`）需转义，避免语法错误或意外结果。策略：将整个用户查询包裹在双引号中禁用语法解析（不损失 trigram 匹配能力）。前端显示 `<mark>` 标签需用 `dangerouslySetInnerHTML` 或自定义渲染组件。

### 9.4 索引管理

- `search.rebuild` → 遍历所有 .md → 对每个 doc_path 独立执行 `BEGIN TRANSACTION; DELETE FROM notes_fts WHERE doc_path=?; INSERT INTO notes_fts ...; COMMIT;`（逐文件事务，不支持整体回滚）。提交到 IO 线程池后台执行（非 CPU 密集），不阻塞 RPC 调用，支持 `$/cancel`——取消在文件间间隙生效（单个文件写入中的事务不可打断）。取消时已完成的文件仍保留在 FTS5 中，不撤销。
- 增量更新：`note.save` 时同步 INSERT/REPLACE
- 索引崩溃 → 用户点「重建索引」即可，不影响 .md 数据
- SQLite WAL 模式保证崩溃后索引完整
- FTS5 写入失败（如连接断开）→ 日志 ERROR + `indexOk=false`，用户可触发 `search.rebuild` 修复
- **FTS5 定期维护：** 每 1000 次写入自动执行一次 `INSERT INTO notes_fts(notes_fts) VALUES('merge')`，防止索引层数过深导致查询退化

---

## 10. 日志

```
%APPDATA%/note-tray/logs/
  sidecar.log + sidecar.{1..5}.log  (10MB × 6 = 60MB max)
  tauri.log   + tauri.{1..5}.log

30天自动清理 | 默认 INFO 级别 | DEBUG 开发模式手动开启
```

---

## 11. 并发模型

```
事件循环线程 (asyncio)  — 帧收发 + 路由分发
  ├── IO 线程池 (max=4)  — 所有文件读写, 图片复制
  ├── CPU 线程池 (max=2) — OCR 单次调用
  └── OCR 预加载线程 (独立) — 进程启动后后台初始化 PaddleOCR 模型，不占用 CPU 线程池

note.save/note.get/search.query 内部通过 run_in_executor 提交到 IO 线程池
search.rebuild 提交到 IO 线程池（非 CPU 密集）
OCR 提取通过 run_in_executor 提交到 CPU 线程池
超时: 查询 5s, OCR 60s, 索引重建无超时
```

---

## 12. 项目结构

### Python 侧

```
note-backend/
  main.py             # 入口, asyncio loop, 方法路由
  models/
    note.py           # front matter 解析, MD 读写
  search.py           # SQLite FTS5 管理 + 查询
  ocr/
    engine.py         # PaddleOCR 封装
  storage/
    fs.py             # 文件系统操作
  logging_config.py   # 日志配置
```

### Tauri 侧

```
note-tray/
  src-tauri/
    main.rs           # 入口
    tray.rs           # 托盘菜单
    hotkey.rs         # 全局热键
    screenshot.rs     # 截图选区
    sidecar.rs        # Python 进程管理 (spawn, 退避)
    rpc.rs            # JSON-RPC 行协议收发
    clipboard.rs      # 剪贴板读取
  src/
    App.tsx
    components/
      CaptureWindow.tsx
      SearchWindow.tsx
      SettingsPage.tsx
```

---

## 13. 非功能需求

### 13.1 性能

| 指标 | 目标 |
|------|------|
| 热键到窗口弹出 | < 100ms |
| 搜索响应 (10k 笔记) | < 500ms |
| OCR 单张截图 | < 2s |
| 启动到可用 | < 500ms |
| 稳态内存 (含 OCR 模型) | < 250MB |
| 安装包体积 | < 80MB |

### 13.2 可靠性

- `.md` 永不损坏 (先写临时文件再 os.replace)
- `note.db` 可随时删除重建
- Python 崩溃自动重启 (含退避)
- 知识库目录丢失 → 提示用户，不崩溃

### 13.3 安全

- 笔记路径限制在知识库根目录内，`note.save` handler 入口先做路径校验，fs.py 二次校验
- stdin/stdout 通信，不监听网络端口
- 前端不直接访问文件系统
- YAML 解析强制使用 `yaml.safe_load()`，禁用 `yaml.load()`（防止 YAML 标签注入）
- 所有文件读写使用 `encoding='utf-8'`，避免 Windows 系统编码（GBK）导致的乱码
- Tauri 侧同时异步读取 stdout 和 stderr，防止单管道缓冲满导致的进程死锁
- 运行期间在 `%APPDATA%/note-tray/tmp/` 创建 `.instance.lock` 文件（PID + flock），防止多实例同时操作同一份数据

---

## 14. 测试

| 层级 | 范围 | 工具 |
|------|------|------|
| 单元 | front matter 解析, FTS5 查询, OCR 封装 | pytest |
| 集成 | RPC handler + 真实文件系统 | pytest + tmp_path |
| E2E | Tauri → Python 全链路 | 手动 |

---

## 15. 二期预留

- **知识图谱：** 正文 `[[笔记名]]` → 解析填充 `links` → 构建图。独立 `graph/` 包。
- **网盘同步：** 将知识库根目录设为网盘同步目录即可。`%APPDATA%/note-tray/` 位于用户目录，不在知识库内，无需额外配置。
- **定时总结合并：** 将一个文件夹下多篇按天积累的笔记合并总结为一篇完整主题笔记（如 `SpringAI/SpringAI-综合.md`），保留原文链接。
