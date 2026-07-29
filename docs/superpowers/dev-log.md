# 衔泥 NestTray 开发日志

> 原名 Note Tray，2026-07-29 正式更名

记录开发过程中的问题、决策、和关键变更。每阶段完成时追加。

---

## 2026-07-27

### 阶段 0 — 初始化

- 创建 `note-backend/` 目录结构（models/ ocr/ storage/ handlers/）
- 创建开发日志 `dev-log.md`
- 计划文档 `docs/superpowers/plans/dev-playbook.md` 已编写完成，包含全部 7 阶段的逐步骤编码清单

### 阶段 1.1 — logging_config.py

- `log_config.py` 实现完成：RotatingFileHandler 10MB×5 + stderr 同步输出 + 30天自动清理 + `encoding='utf-8'`
- `requirements.txt`: 当前 `pyyaml>=6.0`，OCR 依赖留到 Phase 4

### 阶段 1.2 — main.py 骨架

- `main.py` 实现：App 类 + asyncio 事件循环 + stdin/stdout daemon 线程 + JSON-RPC dispatch
- 内置 handler：`system.status` / `system.shutdown` / `$/cancel`
- 顶层异常守卫：try/except → `event.fatal` → 退出

### 阶段 1.3 — 测试与问题记录

**测试结果：** handshake → system.status → system.shutdown 全链路通过 ✅

**修复：**
1. `os.statvfs` 在 Windows 上不存在 → 改为 `shutil.disk_usage()` （跨平台兼容）
2. Python 安装：发现用户系统同时存在 WindowsApps stub 和独立 Python3.12。最终使用 `C:\Users\lixd3\AppData\Local\Programs\Python\Python312\python.exe`，创建了独立 venv

### 阶段 3 — 全文搜索

- `search.py`: SearchIndex 类 (SQLite FTS5 trigram) + doc_meta 表 (note_id → path 映射)
- 支持：upsert / delete / update_path / query (双引号转义) / rebuild (逐文件事务，支持 cancel) / migrate (启动增量 mtime 同步)
- FTS5 merge 自动维护（每 1000 次写入）
- 6 项单元测试全部通过 ✅
- 集成到 main.py：note.save/delete/move 同步 FTS5 + search.query + search.rebuild handlers

### 阶段 4 — OCR 引擎

- `ocr/engine.py`: OcrEngine 类 (PaddleOCR ONNX 封装)
- 构造时自动启动后台预加载线程，完成后推送 event.ocr_ready
- 置信度 < 0.5 丢弃
- handler 集成：ocr.extract / ocr.store / clipboard.ingest_image
- 三种 mode(text/image/both) 完整实现

### 阶段 5 — Tauri 桌面壳

- Rust 模块：main.rs / tray.rs / sidecar.rs / rpc.rs / hotkey.rs / screenshot.rs / clipboard.rs
- 系统托盘 + 全局热键 + Python sidecar 进程生命周期管理
- JSON-RPC 行协议客户端
- 截图：Win+Shift+S → PowerShell 读剪贴板 bitmap → 保存到 tmp/
- 剪贴板：PowerShell GetText

### 阶段 6 — React 捕获窗口 UI

- CaptureWindow.tsx: 三模式 Tab（手输/截图/剪贴板）+ 文件夹输入 + 状态提示
- SearchWindow.tsx: 300ms 防抖 + `<mark>` 渲染 + 结果列表
- SettingsPage.tsx: 重建索引 + 查看日志 + 版本信息

### 阶段 7 — 测试与打包

- **36 项单元测试全部通过** ✅
- 测试覆盖：note 解析/合并/标题提取 (17)、FTS5 搜索/转义 (6)、文件系统路径/原子写 (13)
- PyInstaller `--onedir` 构建成功 ✅
- 基础包体积：23MB（不含 PaddleOCR）
- 已知已知问题：trigger 中文短词召回弱、Windows OCR 未实现、`$/cancel` 线程池不可中断、非原子操作

### 2026-07-29 — 项目更名 + UI 重构

- 正式定名 **衔泥 NestTray**
- 双关含义：衔泥（燕衔泥筑巢）+ Nest（巢穴）+ Tray（系统托盘）
- 全项目更名、UI 全面重构（GitHub Dark 风格）、截图流程重做、PaddleOCR 集成

### 2026-07-29 — 发布准备

- 项目更名 衔泥 NestTray（原 Note Tray）
- Rust release 构建成功（23MB）
- Python 后端 PyInstaller 打包（21MB）
- 发布包 44MB（不含 OCR 模型）
- 删除构建产物：target/ node_modules/ dist/ build/ __pycache__
- 创建 README.md（项目介绍 + 使用指南 + 构建说明）
- 创建 LICENSE（MIT）
- 创建 .gitignore（排除 venv/target/node_modules）
- 创建 pyproject.toml
- 就绪 GitHub 上传
