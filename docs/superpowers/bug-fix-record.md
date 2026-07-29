# Note Tray Bug Fix 记录

记录从开发到联调过程中发现并修复的所有问题。

---

## 1. 架构与编译

### 1.1 Rust 编译：缺少 C 链接器

**症状：** `cargo build` 报 `link: extra operand` 或 `NotAttempted("windres")`

**原因：** 系统没有 MSVC 或 MinGW 工具链。Git Bash 的 `/usr/bin/link` 冲突。

**修复：**
1. 安装 MSYS2 + MinGW-w64: `pacman -S mingw-w64-x86_64-gcc`
2. 在 `build.rs` 中自动添加 MinGW 到 PATH
3. 创建 `.cargo/config.toml` 指定 GNU toolchain
4. 创建 `tauri-dev.cmd` 启动脚本

**文件：** `note-tray/src-tauri/build.rs`, `note-tray/.cargo/config.toml`, `note-tray/tauri-dev.cmd`

### 1.2 侧车进程：Python 找不到

**症状：** `Failed to start NoteTray: sidecar spawn: 目录名称无效`

**原因：** `which("python")` 找到 WindowsApps 桩 (打开 Microsoft Store) 而非真实 Python

**修复：**
1. 跳过 WindowsApps 目录
2. 增加项目 venv 路径回退
3. 增加 `NOTE_PYTHON` / `NOTE_BACKEND_DIR` 环境变量支持
4. 使用 `canonicalize()` 解析后端目录路径

**文件：** `note-tray/src-tauri/src/sidecar.rs`

### 1.3 Tauri 插件配置错误

**症状：** `PluginInitialization("global-shortcut", "invalid type: map, expected unit")`

**原因：** global-shortcut 插件配置格式错误

**修复：** 去掉 global-shortcut 插件 (MVP 用托盘菜单替代)

**文件：** `note-tray/src-tauri/tauri.conf.json`, `note-tray/src-tauri/Cargo.toml`

---

## 2. RPC 通信

### 2.1 RPC 超时 (核心阻塞)

**症状：** 前端操作 5 秒后报 `RPC timeout or cancelled`

**原因：** `_respond` 用 `asyncio.run_coroutine_threadsafe` 把响应放入 `_stdout_queue`，Python event loop 不处理同线程的 `run_coroutine_threadsafe` 回调，导致响应被阻塞。

**修复：** `_respond` 和 `_send_event` 直接写 `sys.stdout.write() + flush()`，绕过 asyncio Queue

**文件：** `note-backend/main.py`

### 2.2 Rust 侧超时太短

**症状：** Python 处理耗时接近 5 秒时 Rust 先超时

**修复：** timeout 从 5s → 10s

**文件：** `note-tray/src-tauri/src/main.rs`

### 2.3 侧车进程崩溃后请求卡死

**症状：** Python 进程崩溃后 Rust 继续发请求，全部 timeout

**修复：** 添加 `alive` AtomicBool 标记，stdout/stderr EOF 时标记为 dead，`call()` 立即返回错误

**文件：** `note-tray/src-tauri/src/rpc.rs`

---

## 3. 编码与国际化

### 3.1 Unicode 非法字符导致崩溃

**症状：** `UnicodeEncodeError: surrogates not allowed` → Python 崩溃 → Rust timeout

**修复：**
1. `storage/fs.py`: `atomic_write` 添加 `errors="replace"`
2. `search.py`: 添加 `_sanitize()` 方法，upsert/commit_batch 统一替换非法字符

**文件：** `note-backend/storage/fs.py`, `note-backend/search.py`

### 3.2 中文乱码

**症状：** 保存到 .md 文件的中文显示为乱码，VSCode 选择 UTF-8 编码后看到错误字符

**原因：** Python 在 Windows 上用 `cp936` (GBK) 读取 stdin，Rust 写入的 UTF-8 JSON 被错误解码

**修复：** Rust 启动 Python 时设置 `PYTHONIOENCODING=utf-8`

**文件：** `note-tray/src-tauri/src/rpc.rs`

### 3.3 时间戳硬编码 UTC+8

**症状：** 时区偏移固定在 +0800

**修复：** 改用 `datetime.now().astimezone()` 自动检测本地时区

**文件：** `note-backend/models/note.py`

---

## 4. SQL / 搜索

### 4.1 FTS5 ALTER TABLE RENAME 不支持

**症状：** 搜索重建策略中 `ALTER TABLE notes_fts_new RENAME TO notes_fts` 对 virtual table 不生效

**修复：** 改为逐文件事务，批量 commit (batch_size=100)

**文件：** `note-backend/search.py`

### 4.2 FTS5 doc_path 未标记 UNINDEXED

**症状：** `doc_path` 被索引，搜索路径名也返回结果

**修复：** 建表 SQL 中 `doc_path UNINDEXED`

**文件：** `note-backend/search.py`

### 4.3 2 字中文搜不到

**症状：** trigram tokenizer 对 2 字符中文查询生成 0 个 trigram

**修复：** FTS5 返回 0 条时，重新隔离 FTS5 操作符 → 走 `LIKE %query%` 文件名匹配

**文件：** `note-backend/search.py`

### 4.4 DB 路径错误

**症状：** `note.db` 创建在 `kb_root/../note.db`，即知识库外一层

**修复：** 改为 `%APPDATA%/note-tray/note.db`

**文件：** `note-backend/main.py`

---

## 5. 笔记引擎

### 5.1 空内容覆盖已有笔记

**症状：** `note.save({content: ""})` 清空已有笔记正文

**修复：** `merge_front_matter` 中判断 `new_content.strip()`，空字符串不覆盖

**文件：** `note-backend/models/note.py`

### 5.2 note.save FTS5 用了原始入参而非合并后的内容

**症状：** 更新笔记后 FTS5 索引与实际文件内容不一致

**修复：** `content` → `upsert_content`，更新路径取 `merged.content`，新建路径取 `note.content`

**文件：** `note-backend/main.py`

### 5.3 clipboard.ingest 缺少搜索索引同步

**症状：** 通过剪贴板/快捷键创建的笔记搜不到

**修复：** `_ingest()` 中追加 `self.search.upsert()`

**文件：** `note-backend/main.py`

### 5.4 note.move 未同步 FTS5

**症状：** 移动笔记后搜索命中旧路径

**修复：** `note.move` handler + `SearchIndex.update_path()`

**文件：** `note-backend/main.py`, `note-backend/search.py`

### 5.5 note.delete 未同步 FTS5

**症状：** 删除笔记后搜索仍返回

**修复：** `note.delete` handler → `self.search.delete()`

**文件：** `note-backend/main.py`

### 5.6 note.save 不保留用户自定义 front matter 字段

**症状：** 用户在 VSCode 中添加 `status: draft` 等字段，note.save 后消失

**修复：** `dump_note` 新增 `raw_yaml` 参数，解析已知字段 + 拼接未知字段

**文件：** `note-backend/models/note.py`

---

## 6. 配置与文件

### 6.1 Config 跨边界写入 `~/.note-tray/`

**症状：** Tauri 写入 `~/.note-tray/config.json` 违反架构边界

**修复：** 移到 `%APPDATA%/note-tray/config.json`，由 Tauri 管理，Python 只读环境变量

**文件：** `note-backend/main.py`, `note-tray/src-tauri/src/tray.rs`

### 6.2 日志路径不一致

**症状：** Python 写 `%APPDATA%` (Roaming)，Rust open_logs 打开 `%LOCALAPPDATA%` (Local)

**修复：** 全部统一到 `%APPDATA%/note-tray/` (使用 `dirs::data_dir()`)

**文件：** `note-tray/src-tauri/src/main.rs`, `note-tray/src-tauri/src/tray.rs`

### 6.3 命名路径重复 `{folder}/{folder}-{date}.md`

**症状：** 嵌套文件夹下路径变为 `SpringAI/Advanced/SpringAI/Advanced-2026-07-26.md`

**修复：** 改为 `{folder}/note-{date}.md`

**文件：** `note-backend/main.py`, `note-backend/search.py`, 前端

---

## 7. 前端

### 7.1 前端调用了不存在的命令

**症状：** `invoke("rpc_call")` → Tauri 无此命令注册

**修复：** 改为 `invoke("backend_call")`，参数用 JSON.stringify 传递

**文件：** `*.tsx`

### 7.2 知识库路径保存调用 system.shutdown

**症状：** 点击"保存"按钮 Python 进程被杀死，RPC 超时

**修复：** 新增 `save_config_cmd` Tauri command，直接写 config.json

**文件：** `note-tray/src-tauri/src/main.rs`, `SettingsPage.tsx`

### 7.3 剪贴板导入无文本导致超时

**症状：** 剪贴板 Tab 点按钮传空 params，RPC 超时

**修复：** 改用 `navigator.clipboard.readText()` 先读文本再传给 backend

**文件：** `CaptureWindow.tsx`

---

## 8. 测试

### 8.1 integration_test.py 路径错误

**症状：** 引用 `tests/main.py` 不存在

**修复：** 改为 `parent.parent / "main.py"`

**文件：** `tests/integration_test.py`

### 8.2 walk_md_files 深度统计 Windows 上错误

**症状：** `rel.count(os.sep)` 用 `\` 但 `Path.relative_to()` 返回 `/`

**修复：** 改为 `rel.replace("\\", "/").count("/")`

**文件：** `note-backend/storage/fs.py`

---

## 9. 其他

### 9.1 `asyncio.Queue` 在事件循环外创建

**症状：** Python 3.12 下可能抛出事件循环未运行异常

**修复：** 移到 `run()` 中创建

**文件：** `note-backend/main.py`

### 9.2 `callable` 作为类型注解

**症状：** `dict[str, callable]` → `NameError: name 'callable' is not defined`

**修复：** 改为 `from collections.abc import Callable`

**文件：** `note-backend/main.py`

### 9.3 `os.statvfs` Windows 不存在

**症状：** `diskFreeBytes` 获取失败

**修复：** 改用 `shutil.disk_usage()`

**文件：** `note-backend/main.py`

### 9.4 截图未插入正文图片引用

**症状：** 图片保存在 `images/xxx.png` 但 Markdown 中不可见

**修复：** 正文自动追加 `![screenshot](images/xxx.png)`

**文件：** `note-backend/main.py`

### 9.5 3 个 Rust warning

**症状：** `save_last_folder` / `save_config` / `child_for_wait` 未使用

**说明：** MVP 预留功能，不影响运行

---

## 文件改动统计

| 目录 | 改动文件数 | 核心文件 |
|------|-----------|---------|
| `note-backend/` | 8 | `main.py`, `search.py`, `models/note.py`, `storage/fs.py`, `ocr/engine.py`, `log_config.py`, `requirements.txt`, `note-backend.spec` |
| `note-backend/tests/` | 4 | `test_note.py`, `test_search.py`, `test_storage.py`, `integration_test.py` |
| `note-tray/src-tauri/` | 7 | `main.rs`, `rpc.rs`, `tray.rs`, `sidecar.rs`, `screenshot.rs`, `build.rs`, `Cargo.toml`, `tauri.conf.json`, `capabilities/default.json` |
| `note-tray/src/` | 4 | `App.tsx`, `CaptureWindow.tsx`, `SearchWindow.tsx`, `SettingsPage.tsx`, `ErrorBoundary.tsx` |
| `note-tray/` | 2 | `.cargo/config.toml`, `tauri-dev.cmd`, `build.bat` |
| `docs/` | 2 | `dev-log.md` (开发日志) |
