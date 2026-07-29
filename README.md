
<p align="center">
  <img src="note-tray/src-tauri/icons/128x128.png" width="64" height="64" alt="NestTray logo" />
  <h1 align="center">衔泥 NestTray</h1>
  <p align="center">极低摩擦的桌面笔记捕获工具 — 截图 · 手输 · 剪贴板 · 全文搜索 · OCR</p>
  <p align="center">
    <img src="https://img.shields.io/badge/platform-Windows-blue" alt="Platform: Windows">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License: MIT">
    <img src="https://img.shields.io/badge/build-passing-brightgreen" alt="Build: Passing">
  </p>
</p>

---

## 简介

衔泥 NestTray 是一个**轻量桌面笔记捕获工具**，运行在系统托盘中，通过全局热键或菜单快速记录知识点，自动归档为纯 Markdown 文件，并支持全文搜索。

**衔泥** — 燕衔泥筑巢，一次一点点，慢慢积累。Nest + Tray = 系统托盘中的知识巢穴。

### 核心特性

- **📷 截图 + OCR** — 截图并提取图片中的文字（支持中文）
- **✏ 手动输入** — 快捷 Markdown 记录，按天自动合并
- **📋 剪贴板导入** — 一键保存剪贴板文本
- **🔍 全文搜索** — SQLite FTS5 引擎，毫秒级响应
- **📂 按项目组织** — 文件夹分类，图片自动归档
- **🗂 纯 Markdown 存储** — 数据自有，离开工具也能用
- **⚡ 极低摩擦** — 全局热键 → 弹窗 → 记录 → 消失

---

## 截图

| 捕获窗口 | 搜索 | 设置 |
|---------|------|------|
| 手输/截图/剪贴板三模式 | 全文搜索 + 高亮片段 | 知识库路径 + 索引管理 |

---

## 快速开始

### 下载安装

直接从 [Releases](../../releases) 下载最新 MSI 安装包或便携版 zip。

### 从源码构建

#### 系统要求

| 组件 | 版本 |
|------|------|
| Rust | 1.70+ |
| Node.js | 18+ |
| Python | 3.10+ |
| MinGW-w64 | (Windows 构建需要) |

#### Windows 构建

```bash
# 1. 安装 MinGW-w64 (使用 MSYS2)
#    从 https://www.msys2.org/ 下载安装，然后：
pacman -S mingw-w64-x86_64-gcc

# 2. 设置 Rust GNU 工具链
rustup default stable-x86_64-pc-windows-gnu

# 3. 安装 Python 依赖
cd note-backend
python -m venv .venv
source .venv/Scripts/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 4. 构建前端 + Rust 后端
cd ../note-tray
npm install

# 开发模式
npm run dev    # 或双击 tauri-dev.cmd

# 构建发布包
npx tauri build
```

> 首次构建 Rust 会下载数百个依赖包，请保持网络畅通。
> 首次运行 PaddleOCR 会自动下载约 200MB 模型到 `~/.paddlex/`。

---

## 项目结构

```
衔泥 NestTray/
├── note-backend/              ← Python 后端 (JSON-RPC sidecar)
│   ├── main.py                # 入口 + 16 个 RPC handlers
│   ├── models/note.py         # YAML front matter + Markdown
│   ├── storage/fs.py          # 原子写入 + 路径校验
│   ├── search.py              # SQLite FTS5 全文搜索
│   ├── ocr/engine.py          # PaddleOCR 封装
│   └── tests/                 # 35 项单元测试
│
├── note-tray/                 ← Tauri 桌面壳 (Rust + React)
│   ├── src-tauri/             # Rust 后端
│   │   ├── src/
│   │   │   ├── main.rs        # 入口 + 3 个 Tauri 命令
│   │   │   ├── rpc.rs         # JSON-RPC 持久客户端
│   │   │   ├── tray.rs        # 系统托盘 + 配置
│   │   │   ├── sidecar.rs     # Python 进程路径解析
│   │   │   └── screenshot.rs  # Windows 系统截图
│   │   └── tauri.conf.json    # Tauri 配置
│   └── src/                   # React 前端
│       ├── CaptureWindow.tsx   # 捕获窗口（手输/截图/剪贴板）
│       ├── SearchWindow.tsx    # 搜索窗口
│       ├── SettingsPage.tsx    # 设置页
│       └── ErrorBoundary.tsx   # 错误边界
│
└── docs/superpowers/           ← 设计文档 & 开发日志
```

---

## 使用指南

### 首次运行

1. 启动后点击 **⚙ 设置**
2. 设置**知识库根目录**（所有笔记存放的位置，如 `D:/notes`）
3. 点击保存 → 重启应用

### 捕获笔记

选择目标文件夹后：

| 模式 | 操作 | 效果 |
|------|------|------|
| **手输** | 输入 Markdown → Ctrl+Enter | 保存到 `{文件夹}/note-{日期}.md` |
| **截图** | 截图 → 提取文字 | 图片 + OCR 文字追加到笔记 |
| **剪贴板** | 复制文本 → 导入 | 剪贴板内容直接保存 |

### 文件夹管理

- 下拉列表搜索已有文件夹
- 输入不存在名称 → 自动新建
- 选中文件夹 → 下拉出现「🗑 删除」选项

### 图片

截图自动保存到 `{文件夹}/images/{日期}-{随机}.png`
Markdown 正文自动插入 `![screenshot](images/xxx.png)`

### 搜索

- 输入关键词 → FTS5 全文搜索（3 字以上中文更准确）
- 2 字中文：自动回退文件名匹配

---

## 技术栈

| 层 | 技术 | 用途 |
|----|------|------|
| 壳 | **Tauri v2** (Rust) | 系统托盘、窗口管理、侧车进程 |
| 前端 | **React + TypeScript** | 捕获窗口 UI |
| 后端 | **Python** (sidecar) | OCR、文件读写、FTS5 搜索 |
| 通信 | **JSON-RPC 2.0** (stdin/stdout) | 进程间调用 |
| 搜索 | **SQLite FTS5** (trigram) | 全文索引 |
| 存储 | **纯文件系统** (.md + YAML) | 数据可迁移 |
| OCR | **PaddleOCR 3.7** | 中英文文字提取 |

---

## 许可证

[MIT](LICENSE)

---

## 致谢

- 图标设计：[你的名字]
