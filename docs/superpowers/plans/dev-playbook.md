# Note Tray 开发实施手册

> 基于 `2026-07-26-note-tray-design-v2.md` (spec) + `2026-07-26-note-tray-plan.md` (plan)  
> 本手册是编码时的逐步骤执行清单，每个阶段完成后才能进入下一阶段。

---

## 工作区布局

```
D:\project\note\
  note-backend/          ← Python sidecar (主开发对象)
  note-tray/             ← Tauri 壳 (React + Rust)
```

---

## 阶段 1：Python 后端骨架

目标：启动 main.py → 握手 → stdin/stdout JSON-RPC 循环可用，PyInstaller 可打包。

### 1.1 初始化工程

**文件创建清单：**

| 文件 | 内容 |
|------|------|
| `note-backend/requirements.txt` | `pyyaml`（基础依赖，OCR 阶段再加 others） |
| `note-backend/logging_config.py` | RotatingFileHandler + stderr 同步输出 |
| `note-backend/main.py` | 入口文件 |

**requirements.txt:**
```
pyyaml>=6.0
```
（OCR 依赖留到阶段 4 添加）

**logging_config.py 关键行为：**
- 日志目录：`%APPDATA%/note-tray/logs/`
- 文件：`sidecar.log` + 5 个轮转文件，每个 10MB
- 级别：默认 INFO，环境变量 `NOTE_DEBUG=1` 时 DEBUG
- 同时输出到 stderr（管道供 Tauri 读取）
- `encoding='utf-8'`
- 30 天自动清理策略：检查文件 mtime，超过 30 天删除

**检查点 1.1：** `python -c "from logging_config import setup_logging; setup_logging()"` 不报错

### 1.2 实现 JSON-RPC 行协议

**main.py 内部结构：**

```
main.py
  ├── class App
  │   ├── __init__(): 创建 IO 池(4), CPU 池(2), 请求表 dict, self.loop (事件循环引用), self.ocr (OcrEngine 实例)
  │   ├── start(): 启动 stdin/stdout 线程 + asyncio loop
  │   ├── _stdin_reader(): daemon 线程函数, readline → asyncio.Queue
  │   ├── _stdout_writer(): daemon 线程函数, Queue → stdout.write(flush=True)
  │   ├── _dispatch(json): 路由 → 调用 handler → 结果写入 stdout Queue
  │   ├── _send_event(method, params): → 构造 notification → 写入 stdout Queue (供线程桥接调用)
  │   └── shutdown(): 取消所有 Future, 关线程池, 关 SQLite
  │
  │  **顶层异常守卫：**
  │    asyncio.run() 外包裹 try/except
  │    → 捕获所有未处理异常 → 日志 FATAL
  │    → emit event.fatal → 等待 100ms → sys.exit(1)
  │
  │  请求表: dict[str, asyncio.Future]  # requestId → Future
  │    - 收到请求时创建 Future，存入请求表
  │    - 收到 $/cancel 时 cancel 对应 Future，完成后从表中移除
  │    - handler 完成后从表中移除
  │
  │  handler 路由: dict[str, callable]
  │    system.shutdown → App.shutdown()
  │    system.status → system_status()
  │    $/cancel → cancel_request()
  │    未知方法 → 返回 -32601
  │
  │  错误响应格式:
  │    {"code": int, "message": str, "data": {"retryable": bool, "userAction": str, "detail": dict}}
```

**具体实现规则：**
- 从 stdin readline，每个请求一行完整 JSON
- method 路由用字典映射
- $/cancel 只需 cancel Future，线程池任务不打断（已知限制）
- `print(json.dumps(resp), flush=True)` 输出响应
- stderr 只用于日志输出

**测试方法：** 手动 pipe：
```bash
echo '{"jsonrpc":"2.0","id":"1","method":"system.status","params":{}}' | python main.py
```

### 1.3 实现 handshake + system.status

**handshake（Python 进程启动后第一帧）：**
```python
# 在 App.start() 中，事件循环开始前发送
hello = {
    "jsonrpc": "2.0",
    "method": "backend.hello",
    "params": {
        "version": "0.1.0",
        "protocolVersion": 1,
        "capabilities": ["note", "search", "ocr"]
    }
}
print(json.dumps(hello), flush=True)
```

**system.status handler：**
```python
async def system_status(app) -> dict:
    return {
        "ok": True,  # 初始为 True；启动时 kb 路径失效则为 False
        "indexOk": False,  # 阶段3 实现后更新
        "ocrOk": False,    # 阶段4 实现后更新
        "ocrEngine": "loading",  # "loading"|"paddleocr"|"unavailable"；启动后预加载立即开始
        "protocolVersion": 1,
        "rootPath": os.environ.get("NOTE_KB_ROOT", ""),
        "diskFreeBytes": get_disk_free(rootPath),
        "totalNotes": 0  # 阶段2 实现后更新
    }
```

**shutdown 清理流程：**
1. 设置 `app._shutdown = True`（阻止新请求分发）
2. Cancel 所有 in-flight Future
3. 等待 IO 线程池关闭（timeout 5s）
4. 等待 CPU 线程池关闭（timeout 5s）
5. 关闭 SQLite 连接（如果已打开）
6. 退出进程

**知识库目录初始化校验：**
- 启动时读 `NOTE_KB_ROOT` 环境变量
- 检查目录是否存在、可读写
- 不可用 → `system.status` 返回 `ok=False` + 日志 WARN，不等写操作才报错

### 1.4 并发骨架

```python
# 在 App.__init__ 中
self.io_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="io")
self.cpu_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cpu")

# handler 执行模式：
# IO 操作 → loop.run_in_executor(self.io_pool, fn)
# CPU 操作 → loop.run_in_executor(self.cpu_pool, fn)

# 超时：
# 通用查询 (note.get/search.query 等): asyncio.wait_for(5s)
# OCR 调用: asyncio.wait_for(60s)
# 索引重建: 无超时
```

**验收：** `python main.py` 启动，手动 echo JSON 到 stdin。验证：
- stdout 收到 `backend.hello`
- 请求 `system.status` 收到正确响应
- 发送 `system.shutdown` 后进程退出

---

## 阶段 2：笔记引擎

### 2.1 front matter 解析 (`models/note.py`)

```python
def parse_note(content: str) -> Note
    # 1. 用正则 `^---\s*\n(.*?)\n---\s*\n` (re.DOTALL) 匹配开头
    # 2. yaml.safe_load(front_matter_yaml) 解析 YAML
    # 3. 剩余部分作为正文
    # 4. 返回 Note 对象

def make_note(content: str, tags: list[str]|None=None) -> Note
    # 生成新 Note：
    #   schemaVersion=1
    #   id = "n_" + secrets.token_hex(4)  (8 hex)
    #   createdAt = now(ISO8601+tz)
    #   updatedAt = now
    #   title = content 首行去掉 "# "，无则空
    #   tags = tags or []
    #   attachments = []

def dump_note(note: Note) -> str
    # yaml.dump(front_matter, allow_unicode=True, default_flow_style=False) + "\n" + content
    # 注意：pyyaml 的 default_flow_style=False 输出块样式
    
def extract_title(content: str) -> str|None
    # 匹配 content 首行 "# " 开头
    # 返回 "# " 后的文本，strip
    # 无匹配返回 None

def merge_front_matter(existing: Note, new_tags: list[str]|None) -> Note
    # 保留：id, createdAt, links, attachments
    # 更新：updatedAt = now
    # 覆盖：tags = new_tags (如果 new_tags 不为 None)
    # 保留所有未知字段（不解析也能保留——用 YAML 的 RoundTripLoader 或手动 split 再拼接）
    # 更安全的做法：用 regex 匹配 front matter 块，仅修改已知行的值，未知行保持原样
```

**YAML 字段约束：**
- `yaml.safe_load()` 禁止 `yaml.load()`
- `schemaVersion` 缺失时视为 v0，MVP 自动升级到 v1（写入 front matter 再写回）
- `title` 自动从 content 首行 `# ` 提取（仅在 note.save 新建时，已有文件保留原 title）
- `tags` 在 YAML 中为 `[tag1, tag2]` 列表格式

### 2.2 文件系统操作 (`storage/fs.py`)

```python
def atomic_write(path: Path, content: str) -> None
    # 1. os.makedirs(path.parent, exist_ok=True)
    # 2. tmp = path.with_suffix(path.suffix + ".tmp." + token_hex(4))
    # 3. with open(tmp, 'w', encoding='utf-8') as f:
    #       f.write(content)
    #       f.flush()
    #       os.fsync(f.fileno())
    # 4. os.replace(tmp, path)

def validate_path(kb_root: Path, target: Path) -> Path
    # os.path.realpath 解析两个路径
    # 校验 target 以 kb_root 为前缀
    # 不通过 → raise ValueError(NOTE_PATH_INVALID)

def read_file(path: Path) -> tuple[str, dict|None]
    # with open(path, 'r', encoding='utf-8') as f:
    #     content = f.read()
    # front_matter = parse_front_matter(content)
    # body = strip_front_matter(content)
    # return (body, front_matter)
    # 文件不存在 → raise FileNotFoundError
    # 没有 front matter → return (content, None)

def delete_file(path: Path) -> None
    # path.unlink(missing_ok=True)

def rename_path(from_path: Path, to_path: Path) -> None
    # validate 两个路径
    # to 已存在 → raise FileExistsError
    # os.rename(from, to)
```

**路径校验规则：**
```python
def assert_in_kb(kb_root: str, target: str) -> str:
    kb_real = os.path.realpath(kb_root)
    t_real = os.path.realpath(os.path.join(kb_root, target))
    if not t_real.startswith(kb_real + os.sep) and t_real != kb_real:
        raise PermissionError("路径不在知识库内")
    return t_real
```

### 2.3 note.save handler

```python
async def handle_note_save(app, params):
    # params: {path, content, tags?}
    # 1. validate_path(kb_root, params["path"])
    # 2. 检查目标文件是否存在
    # 3. 不存在 → make_note(content, tags) → atomic_write
    # 4. 存在 → 读取 → merge_front_matter → 替换 content → atomic_write
    # 5. 不存在且 content 为空 → return PARSE_ERROR
    # 6. 返回 {"noteId": note.id, "savedAt": note.updatedAt, "path": path}
    #
    # 线程：run_in_executor(io_pool)
    # 超时：5s
```

### 2.4 上层业务接口

**note.get:**
```python
# params: {path}
# validate_path → read_file → parse_note
# return: {noteId, path, content, frontMatter}
```

**note.delete:**
```python
# params: {path}
# 1. validate_path
# 2. 解析 front matter 获取 attachments[].path
# 3. 逐一删除图片文件 (不存在则 WARN 不中断)
# 4. delete_file(.md)
# 5. 同步从 FTS5 删除 (调 search.py 的 delete_index)
# return: {}
```

**note.move:**
```python
# params: {from, to}
# 1. validate both paths
# 2. to 已存在 → return NOTE_MOVE_DEST_EXISTS
# 3. from 不存在 → return NOTE_NOT_FOUND
# 4. from == to → return {} (no-op)
# 5. rename_path(from, to)
# 6. 同步更新 FTS5 doc_path
# return: {}
```

**clipboard.ingest:**
```python
# params: {text, folder?, tags?}
# 1. text 为空 → return PARSE_ERROR
# 2. folder 默认用 lastFolder (Tauri 传) 或根目录
# 3. 目标路径 = f"{folder}/note-{today}.md"
# 4. 文件不存在 → note.save(path, text, tags)
# 5. 文件已存在 → 读取原内容 → 追加 "\n\n---\n\n## {HH:MM}\n\n{text}" → note.save
# 6. tags 透传到 note.save
# return: {"noteId": ...}
```

**clipboard.ingest_image:**
```python
# params: {imagePath, folder?, mode}
# 内部调 ocr.store (目前返回占位)
```

**note.list_folder:**
```python
# params: {folder}
# 遍历 {kb_root}/{folder}/*.md
# 对每个文件：读取前~200字节 → 提取 title + 前100纯文字符 snippet
# 按 mtime 降序排列
# return: {notes: [{path, title, updatedAt, snippet}]}
```

**browser.tree:**
```python
# params: {root?}
# 递归遍历知识库目录
# 深度限制 3 层
# 同层级按文件名拼音序排列
# return: {tree: [{name, path, type: "file"|"dir", children?}]}
```

**attachment.list:**
```python
# params: {noteId}
# 1. 在 doc_meta 表中存有 noteId → doc_path 映射（注：doc_meta 在 stage 3 建立的）
#    SELECT doc_path FROM doc_meta WHERE note_id = ?
#    注：doc_meta 表结构见阶段 3（含 note_id 列）
# 2. read_file(doc_path) → 解析 front matter → 返回 attachments
# 3. 文件不存在或 noteId 不匹配 → NOTE_NOT_FOUND
# return: {attachments: [{hash, path, ocrText}]}
```

### 检查点 2.1 + 2.2 (M1 打包验证)

```bash
cd note-backend
pip install pyyaml
pip install pyinstaller
pyinstaller --onedir main.py
# 验证：dist/main/main.exe 启动后 hello 正常
# 验证：echo JSON → stdin → 收到响应
# 验证：dist 体积 <= 10MB (只有 pyyaml，无 paddle)
```

---

## 阶段 3：搜索

### 3.1 SQLite FTS5 索引管理 (`note-backend/search.py`)

```python
class SearchIndex:
    def __init__(self, db_path: str):
        # ... (原有初始化代码)
        # self._write_counter = 0  # 自增计数器，每 1000 触发一次 merge

    def upsert(self, doc_path: str, title: str, content: str, ocr_text: str):
        # ... (原有 upsert 逻辑)
        # self._maybe_merge()

    def delete(self, doc_path: str):
        # ... (原有 delete 逻辑)
        # self._maybe_merge()

    def _maybe_merge(self):
        # self._write_counter += 1
        # if self._write_counter >= 1000:
        #     self._write_counter = 0
        #     conn.execute("INSERT INTO notes_fts(notes_fts) VALUES('merge')")

    def query(self, q: str, limit: int=20, offset: int=0) -> dict:
        # 查询转义：用双引号包裹整个 q (禁用 FTS5 语法解析)
        # safe_q = f'"{q.replace(\'"\', \'\'\')}"'
        # with lock:
        #   cursor = conn.execute(
        #       "SELECT doc_path, title, snippet(...), bm25(notes_fts) "
        #       "FROM notes_fts WHERE notes_fts MATCH ? "
        #       "ORDER BY bm25(notes_fts) LIMIT ? OFFSET ?",
        #       (safe_q, limit, offset)
        #   )
        #   # search.query 返回后，从 front matter 补入 updatedAt
        # return {results, totalHits, timeMs}

    def rebuild(self, app, cancel_event: threading.Event):
        # for each .md in kb_root:
        #   if cancel_event.is_set(): break
        #   BEGIN; DELETE; INSERT; COMMIT;  (逐文件事务)
        # return: None

    def migrate(self, walk_files: callable):
        # 启动时调用的增量同步。
        # 依赖 doc_meta 表记录的文件 mtime，与文件系统对比。
        # 1. 遍历 kb_root/*.md
        # 2. 对每个文件：读 .md → 解析 front matter → 取 id + mtime
        # 3. SELECT mtime FROM doc_meta WHERE doc_path = ?
        #    若 file_mtime > db_mtime（或记录不存在），则调用 upsert()
        # 4. 记录数量差异更新 totalNotes
        # 首次启动（doc_meta 空）→ 全量遍历；后续增量只需对比 mtime。

    def merge(self):
        # 每 1000 次写入调用：
        # conn.execute("INSERT INTO notes_fts(notes_fts) VALUES('merge')")
```

**关于 `totalNotes` 矫正：** 在 `SearchIndex` 中维护一张 `doc_meta` 表：

```sql
CREATE TABLE IF NOT EXISTS doc_meta (
    doc_path TEXT PRIMARY KEY,
    note_id TEXT UNIQUE,      -- front matter 中的 id，用于 attachment.list 查找
    file_mtime REAL,          -- os.path.getmtime() 值，用于增量同步
    word_count INTEGER
);
```

- `note.save` 成功后: `INSERT OR REPLACE INTO doc_meta VALUES (?, ?, ?, ?)`
- `note.delete` 后: `DELETE FROM doc_meta WHERE doc_path = ?`
- `note.move` 后: `UPDATE doc_meta SET doc_path = ? WHERE doc_path = ?`
- 每 30 分钟遍历文件系统与 `doc_meta` 对比，差异则更新 `totalNotes`。

### 3.2 集成 handlers

```python
async def handle_search_query(app, params):
    # q, limit?, offset?
    # 调用 SearchIndex.query()
    # 对每个 result: note.get(path) 补入 updatedAt

async def handle_search_rebuild(app, params):
    # 提交到 IO 线程池后台执行
    # 完成后推送 event.index.rebuilt
    # 支持 cancel (通过 cancel_event)

async def handle_note_save(app, params):
    # ... (现有笔记保存逻辑)
    # 成功后: app.search.upsert(doc_path, title, content, ocr_text)
```

**验收：**
```bash
# 1. 创建一篇笔记
echo '{"jsonrpc":"2.0","id":"1","method":"note.save","params":{"path":"test/note-2026-07-27.md","content":"# 测试\n\n搜索测试内容"}}' | python main.py
# 2. 搜索命中
echo '{"jsonrpc":"2.0","id":"2","method":"search.query","params":{"q":"测试"}}' | python main.py
# 3. 验证结果包含 path + snippet + score
```

---

## 阶段 5：Tauri 桌面壳

注意：阶段编号与 plan 一致，阶段 4 (OCR) 延后。

### 5.1 初始化 Tauri 工程

```bash
npm create tauri-app@latest note-tray -- --template react-ts
cd note-tray
npm install
```

**tauri.conf.json sidecar 配置：**
```json
{
  "bundle": {
    "externalBin": ["binaries/python-sidecar"],
    "windows": {
      "wix": { ... }
    }
  }
}
```

开发期：sidecar 指向 `python main.py`
打包期：sidecar 指向 PyInstaller 产物

### 5.2 系统托盘 (`tray.rs`)

- 使用 `tray-icon` crate 或 Tauri v2 tray plugin
- 菜单项：快速笔记、截图笔记、打开知识库、搜索、设置、日志、退出
- 状态图标：正常=绿色、Python 不可用=黄色、5 次崩溃=红色
- 红色状态点击弹出「后端异常，点击重试」对话框

### 5.3 全局热键 (`hotkey.rs`)

- 默认 `Ctrl+Shift+N`
- 使用 `global-hotkey` 或 Tauri v2 global-shortcut plugin
- 热键按下 → 通知 React 前端弹出捕获窗口

### 5.4 Python 进程管理 (`sidecar.rs`)

```rust
struct Sidecar {
    process: CommandChild,
    rpc: RpcClient,
}

impl Sidecar {
    fn spawn(kb_root: &str) -> Self {
        // Command::new("python/main.exe")
        //     .env("NOTE_KB_ROOT", kb_root)
        //     .spawn()
        // 异步读取 stdout 和 stderr
    }
    
    fn wait_handshake(timeout: Duration) -> Result<ProtocolVersion> {
        // 读 stdout 第一帧 → 解析 backend.hello
        // 匹配 protocolVersion，不兼容则 shutdown + 提示升级
    }
    
    fn health_check() { /* 每 30s system.status */ }
    
    fn restart_with_backoff() {
        // 退避: 1s → 5s → 15s → 60s → 放弃
        // 5次失败后托盘变红，用户手动重试
    }
}
```

**单实例锁：** Python 侧启动时在知识库根目录创建 `.note.lock` 文件锁：
```python
# main.py 启动时
lock_path = os.path.join(kb_root, ".note.lock")
try:
    # Windows: msvcrt.locking()
    # 跨平台方案: 用 file.write(str(pid)) + os.fsync + 进程退出时清理
except:
    sys.exit("另一实例正在使用此知识库")
```

### 5.5 JSON-RPC 客户端 (`rpc.rs`)

```rust
struct RpcClient {
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
    pending: HashMap<String, oneshot::Sender<Response>>,
    next_id: AtomicU64,
}

impl RpcClient {
    fn send_request(&mut self, method: &str, params: Value) -> impl Future<Response> {
        // 1. id = self.next_id.fetch_add(1)
        // 2. 构造 JSON-RPC 请求
        // 3. 写入 stdin (一行 JSON)
        // 4. 创建 oneshot channel，存入 pending
        // 5. 返回 receiver
    }
    
    fn read_loop(&mut self) {
        // 后台线程：逐行读 stdout
        // 每行是一个 JSON-RPC 响应或事件
        // 有 id → 从 pending 取出 sender → 发送 response
        // 无 id (notification) → 判断 event 类型 → 通知前端
    }
}
```

**超时与重试：**
```rust
// 查询请求 5s 超时，OCR 60s
// retryable=true 的错误自动重试 1 次
// retryable=false 直接抛给用户
```

### 5.6 截图选区 (`screenshot.rs`)

```rust
fn take_screenshot() -> Result<PathBuf> {
    // 1. 模拟 Win+Shift+S (keybd_event)
    // 2. 轮询剪贴板检测新图片 (每 200ms，最多 10s)
    // 3. 检测到图片 → 保存到 %APPDATA%/note-tray/tmp/screenshot_{timestamp}.png
    // 4. 返回图片路径
    // 5. 操作完成立即删除 tmp/ 下的该图片
}
```

### 5.7 剪贴板读取 (`clipboard.rs`)

```rust
fn get_clipboard() -> ClipboardResult {
    // 检测剪贴板类型：
    //   文本 → 传给前端，走 clipboard.ingest RPC
    //   图片 → 保存到 tmp/，走 clipboard.ingest_image RPC
    //   空 → 通知前端显示"剪贴板为空"
}
```

**临时文件清理策略（统一）：**
- 正常路径：单次操作完成后立即删除
- 兜底路径：启动时清理超过 24h 的残留

---

## 阶段 6a：React 捕获窗口（基础 UI）

### 捕获窗口 (`CaptureWindow.tsx`)

```tsx
// 400×300 固定尺寸，屏幕居中，置顶
// 三模式 Tab: [截图] [手输] [剪贴板]
// Tab 默认 = 截图模式

// 启动态:
//   Python 未握手 → 显示 "启动中…"
//   超过 5s 未握手 → 显示 "后端启动超时，是否重试?"
```

### 文件夹选择器

```tsx
// 下拉列表：调用 browser.tree 获取已有文件夹，按最近使用排序
// 输入新名称 → 自动创建新文件夹
// 选中后侧边显示该文件夹最近 5 篇笔记 (note.list_folder)
// 本地缓存最近使用的文件夹列表 (localStorage)
```

### 手输模式

```tsx
// Markdown 编辑区 (textarea + 轻量语法高亮)
// Ctrl+V 粘贴图片 → 走 OCR 流程
// Ctrl+Enter → 调 clipboard.ingest (文本) 或 ocr.store (图片)
```

### 搜索窗口 (`SearchWindow.tsx`)

```tsx
// 输入框 + 结果列表
// 300ms 防抖
// 显示: title, path, highlighted snippet, updatedAt
// Enter → 系统默认编辑器打开文件 (Tauri shell.open)
// Ctrl+C → 复制路径
// 搜索结果中的 <mark> 标签用 dangerouslySetInnerHTML 渲染
```

### 设置页 (`SettingsPage.tsx`)

```tsx
// 知识库根目录选择 (Tauri dialog API)
// 热键自定义 (预设 3 组 + 自由输入)
// "重建索引" 按钮 → search.rebuild
// "查看日志" 按钮 → shell.open 打开日志目录
// 配置写回 config.json (Tauri 侧)
```

---

## 阶段 4：OCR 引擎

### 4.1 PaddleOCR 封装 (`ocr/engine.py`)

```python
class OcrEngine:
    def __init__(self):
        self.state = "loading"  # "loading"|"paddleocr"|"unavailable"，构造即启动预加载
        self._engine = None
        self._preload_thread = None
        self._start_preload()  # 构造时自动启动后台预加载

    def _start_preload(self):
        # 独立后台线程：
        #   self._preload_thread = threading.Thread(target=self._preload_worker, daemon=True)
        #   self._preload_thread.start()

    def _preload_worker(self):
        # 在线程中执行耗时加载，完成后桥接回 asyncio 发送 event.ocr_ready
        # try:
        #     from paddleocr import PaddleOCR
        #     self._engine = PaddleOCR(use_angle_cls=True, lang='ch', use_onnx=True)
        #     self.state = "paddleocr"
        #     # 桥接回事件循环 (self._loop 是 App 的 asyncio loop):
        #     asyncio.run_coroutine_threadsafe(
        #         self._send_event("event.ocr_ready"),
        #         self._loop
        #     )
        # except Exception as e:
        #     logger.warn(f"PaddleOCR 加载失败: {e}")
        #     self.state = "unavailable"
        #
        # 线程→asyncio 桥接: run_coroutine_threadsafe(coro, loop)
        # _send_event() 构造 JSON-RPC notification 写入 stdout Queue

    def extract(self, image_path: str) -> dict:
        # 读取图片 → 调 PaddleOCR → 拼接文字
        # 置信度 < 0.5 的丢弃
        # return {"text": ..., "confidence": ..., "engine": "paddleocr", "timeMs": ...}
        # engine 不可用 → raise OcrNotReadyError
```

### 4.2 OCR handlers

**ocr.extract:**
```python
# params: {imagePath}
# 检查图片大小 ≤ 20MB → 否则 OCR_IMAGE_TOO_LARGE
# 检查格式 PNG/JPG → 否则 OCR_UNSUPPORTED_FORMAT
# ocrEngine.extract(image_path) → 返回结果
# 超时 60s
```

**ocr.store:**
```python
# params: {imagePath, folder?, mode}
# 1. copy image to {folder}/images/{date}-{8随机hex}.png
# 2. ocrEngine.extract(image_path) → text
# 3. mode 分支:
#    "text": 仅 clipboard.ingest(text, folder) → 不存附件
#    "image": 追加附件到 front matter → note.save → 不提取文字
#    "both": 存附件(含ocrText) + clipboard.ingest(text, folder)
```

### OCR 状态集成

```python
# system.status 中：
#   ocrOk = engine.state == "paddleocr"  # loading 和 unavailable 都返回 False
#   ocrEngine = engine.state  (显示 "loading"/"paddleocr"/"unavailable")
# engine.state 变化时 → 线程内通过 asyncio.run_coroutine_threadsafe() 向事件循环投递事件
```

---

## 阶段 6b：截图 + OCR UI

### 截图模式

```tsx
// 1. 用户点 "截图"
// 2. 调 Rust 层的 take_screenshot()
// 3. Rust 调用 Win+Shift+S → 轮询剪贴板 → 返回图片路径
// 4. 显示缩略图
// 5. 用户选 mode: [提取文字] / [保存附件] / [两者]
// 6. 选目标文件夹
// 7. Ctrl+Enter → 调 ocr.store({imagePath, folder, mode})
// OCR 按钮状态：
//   "loading" → 显示 "初始化 OCR…" + spinner
//   "paddleocr" → 正常可用
//   "unavailable" → 置灰 + tooltip "OCR 不可用"
```

### 剪贴板模式

```tsx
// 自动检测剪贴板内容
// 图片 → ocr.store (同截图流程)
// 文本 → clipboard.ingest
```

---

## 阶段 7：测试与打包

### 单元测试 (pytest + tmp_path)

```python
# 测试 front matter 解析:
#   test_parse_empty_front_matter
#   test_parse_missing_front_matter
#   test_parse_tags_attachments
#   test_dump_roundtrip
#   test_title_extraction

# 测试原子写入:
#   test_atomic_write_creates_file
#   test_atomic_write_replaces_content
#   test_concurrent_writes (验证文件级锁)

# 测试路径校验:
#   test_allow_subdir
#   test_reject_dotdot
#   test_reject_symlink_escape

# 测试 FTS5:
#   test_upsert_and_query
#   test_query_sanitization
#   test_rebuild

# 测试错误码:
#   test_note_not_found
#   test_disk_full
#   test_path_invalid
```

### 集成测试

```python
# 启动 Python 进程 → 通过 stdin 发送 JSON-RPC → 验证 stdout
# 覆盖全部 16 个方法
# 场景：note.save → note.get → note.list_folder → note.move → note.delete
# 场景：clipboard.ingest → search.query → 验证 FTS5 同步
# 场景：Python 崩溃后重启验证
```

### PyInstaller 打包

```bash
# --onedir 模式 (启动快)
pyinstaller --onedir --add-data "path/to/paddle;." main.py
# 体积目标: < 80MB (含 PaddleOCR 约 60-80MB)
```

### 文档

- 已知问题列表：trigram 中文 2 字词召回弱、Windows OCR 暂未实现、`$/cancel` 线程池任务不可中断、文件操作与 FTS5 非原子、OCR 首次模型下载无进度反馈
- Spec 状态更新为「已实现」

---

## 里程碑检查清单

| 里程碑 | 通过条件 |
|--------|---------|
| **M1** | `pyinstaller --onedir main.exe` → exe 可独立启动、hello 正常、体积 ≤ 10MB |
| **M3** | Tauri → Python 全链路：手输文本 → 保存为 .md → 搜索命中 |
| **最终** | 全部 16 个 RPC 方法通过集成测试；全部异常路径覆盖 |

---

## 16 个 RPC handler 对照

| # | 方法 | 实现位置 | 依赖 |
|---|------|---------|------|
| 1 | `note.save` | handlers/ 或 main.py | 阶段 2 |
| 2 | `note.get` | handlers/ 或 main.py | 阶段 2 |
| 3 | `note.delete` | handlers/ 或 main.py | 阶段 2, 3 |
| 4 | `note.move` | handlers/ 或 main.py | 阶段 2, 3 |
| 5 | `note.list_folder` | handlers/ 或 main.py | 阶段 2 |
| 6 | `browser.tree` | handlers/ 或 main.py | 阶段 2 |
| 7 | `search.query` | handlers/ 或 search.py | 阶段 3 |
| 8 | `search.rebuild` | handlers/ 或 search.py | 阶段 3 |
| 9 | `ocr.extract` | handlers/ 或 ocr/ | 阶段 4 |
| 10 | `ocr.store` | handlers/ 或 ocr/ | 阶段 4 |
| 11 | `attachment.list` | handlers/ 或 main.py | 阶段 2 |
| 12 | `system.status` | main.py | 阶段 1 |
| 13 | `system.shutdown` | main.py | 阶段 1 |
| 14 | `clipboard.ingest` | handlers/ 或 main.py | 阶段 2 |
| 15 | `clipboard.ingest_image` | handlers/ 或 main.py | 阶段 4 |
| — | `$/cancel` | main.py (dispatch 层) | 阶段 1 |
