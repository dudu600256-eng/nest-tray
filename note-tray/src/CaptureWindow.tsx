import { invoke } from "@tauri-apps/api/core";
import { useState, useEffect, useCallback, useRef } from "react";

type Tab = "text" | "screenshot" | "clipboard";
interface TreeItem { name: string; path: string; type: "dir" | "file"; children?: TreeItem[]; }

const accent = "#3b82f6";
const surface = "#161b22";
const border = "#30363d";
const textDim = "#8b949e";
const inputBg = "#0d1117";

function collectFolders(items: TreeItem[]): string[] {
  const folders: string[] = [];
  for (const item of items) {
    if (item.type === "dir" && item.name !== "images") {
      folders.push(item.path);
      if (item.children) folders.push(...collectFolders(item.children || []));
    }
  }
  return folders.sort((a, b) => a.localeCompare(b));
}

function folderDisplayName(path: string): string { return path.split("/").pop() || path; }
function folderDepth(path: string): number { return path.split("/").length - 1; }

function SearchableFolderSelect({folders, value, onChange, onDeleteFolder}: {
  folders: string[]; value: string; onChange: (v: string) => void; onDeleteFolder?: (f: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const filtered = search ? folders.filter((f) => f.toLowerCase().includes(search.toLowerCase())) : folders;

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => { if (containerRef.current && !containerRef.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);
  useEffect(() => { if (open) setTimeout(() => inputRef.current?.focus(), 50); }, [open]);

  const displayText = value || "请选择项目";
  const displayPart = displayText.split("/").slice(-1)[0];

  return (
    <div ref={containerRef} style={{ position: "relative", flex: 1 }}>
      <div onClick={() => { setOpen(!open); setSearch(""); }} style={{ display: "flex", alignItems: "center", gap: 6, padding: "8px 10px", background: inputBg, border: `1px solid ${border}`, borderRadius: 6, cursor: "pointer", userSelect: "none" }}>
        <span style={{ flex: 1, fontSize: 12, color: value ? "#e6edf3" : textDim }}>{value ? displayPart : "请选择项目"}</span>
        <span style={{ fontSize: 9, color: textDim }}>{open ? "▲" : "▼"}</span>
      </div>
      {open && (
        <div style={{ position: "absolute", top: "100%", left: 0, right: 0, zIndex: 100, marginTop: 4, background: surface, border: `1px solid ${border}`, borderRadius: 6, overflow: "hidden", boxShadow: "0 8px 24px rgba(0,0,0,0.4)" }}>
          <input ref={inputRef} placeholder="搜索或新建文件夹…" value={search} onChange={(e) => setSearch(e.target.value)} style={{ width: "100%", padding: "8px 10px", border: "none", borderBottom: `1px solid ${border}`, background: inputBg, color: "#e6edf3", fontSize: 12, outline: "none" }} />
          <div style={{ maxHeight: 180, overflowY: "auto", padding: 4 }}>
            <OptionItem active={value === ""} onClick={() => { onChange(""); setOpen(false); }} style={{ color: textDim }}>（根目录）</OptionItem>
            {filtered.map((f) => (
              <OptionItem key={f} active={value === f} onClick={() => { onChange(f); setOpen(false); }}>
                <span style={{ marginLeft: folderDepth(f) * 14, fontSize: 12 }}>{folderDepth(f) > 0 ? "└ " : ""}{folderDisplayName(f)}</span>
              </OptionItem>
            ))}
            {search && !filtered.includes(search) && (
              <OptionItem active={false} onClick={() => { onChange(search); setOpen(false); }} style={{ color: accent }}>
                ✏ 新建 "{search}"
              </OptionItem>
            )}
            {value && !search && (
              <>
                <div style={{ height: 1, background: border, margin: "4px 0" }} />
                <OptionItem active={false} onClick={() => { onDeleteFolder?.(value); setOpen(false); }} style={{ color: "#f85149" }}>
                  🗑 删除 "{value}"
                </OptionItem>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function OptionItem({active, onClick, children, style}: {active: boolean; onClick: () => void; children: React.ReactNode; style?: React.CSSProperties}) {
  return (
    <div onClick={onClick} style={{ padding: "6px 10px", borderRadius: 4, cursor: "pointer", background: active ? "#1f2937" : "transparent", color: active ? "#e6edf3" : textDim, fontSize: 12, ...style }}
      onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = "#1c2333"; }}
      onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = "transparent"; }}
    >{children}</div>
  );
}

export default function CaptureWindow() {
  const [tab, setTab] = useState<Tab>("text");
  const [text, setText] = useState("");
  const [folder, setFolder] = useState("");
  const [folders, setFolders] = useState<string[]>([]);
  const [status, setStatus] = useState("");
  const [screenshotPath, setScreenshotPath] = useState<string | null>(null);
  const [ocrText, setOcrText] = useState("");
  const [ocrStep, setOcrStep] = useState<"idle" | "captured" | "extracting" | "preview">("idle");

  const loadFolders = useCallback(async () => {
    try { const r = JSON.parse(await invoke("backend_call", { method: "browser.tree", params: "{}" }) as string); if (r.tree) setFolders(collectFolders(r.tree)); } catch {}
  }, []);
  useEffect(() => { loadFolders(); }, [loadFolders]);

  const handleSave = async () => {
    if (!text.trim()) { setStatus("输入内容为空"); return; }
    setStatus("保存中…");
    try {
      const r = JSON.parse(await invoke("backend_call", { method: "clipboard.ingest", params: JSON.stringify({ text, folder: folder || undefined }) }) as string);
      setStatus(r.noteId ? "✅ 已保存" : "❌ 保存失败");
      if (r.noteId) { setText(""); loadFolders(); }
    } catch (e) { setStatus(`❌ 错误: ${e}`); }
  };

  const handleScreenshot = async () => {
    setStatus("截图进行中…");
    try {
      const path = await invoke("take_screenshot") as string;
      setScreenshotPath(path);
      setOcrStep("captured");
      setStatus("");
    } catch (e) { setStatus(`截图失败: ${e}`); }
  };

  const handleExtractOcr = async () => {
    if (!screenshotPath) return;
    setOcrStep("extracting"); setStatus("提取文字中…");
    try {
      const r = JSON.parse(await invoke("backend_call", { method: "ocr.extract", params: JSON.stringify({ imagePath: screenshotPath }) }) as string);
      setOcrText(r.text || "");
      setOcrStep("preview"); setStatus("");
    } catch (e) { setStatus("OCR 不可用，将仅保存图片"); setTimeout(() => handleSaveImageOnly(), 500); }
  };

  const handleSaveImageOnly = async () => {
    if (!screenshotPath) return;
    setStatus("保存中…");
    try {
      const r = JSON.parse(await invoke("backend_call", { method: "ocr.store", params: JSON.stringify({ imagePath: screenshotPath, folder: folder || undefined, mode: "image" }) }) as string);
      setStatus(r.noteId ? "✅ 已保存" : "❌ 保存失败");
      resetScreenshot(); if (r.noteId) loadFolders();
    } catch (e) { setStatus(`保存失败: ${e}`); }
  };

  const handleOcrSave = async () => {
    if (!screenshotPath) return;
    setStatus("保存中…");
    try {
      const r = JSON.parse(await invoke("backend_call", { method: "ocr.store", params: JSON.stringify({ imagePath: screenshotPath, folder: folder || undefined, mode: "both" }) }) as string);
      setStatus(r.noteId ? "✅ 已保存" : "❌ 保存失败");
      resetScreenshot(); if (r.noteId) loadFolders();
    } catch (e) { setStatus(`保存失败: ${e}`); }
  };

  const handleClipboard = async () => {
    setStatus("读取剪贴板…");
    try {
      const clipText = await navigator.clipboard.readText();
      if (!clipText.trim()) { setStatus("剪贴板为空"); return; }
      const r = JSON.parse(await invoke("backend_call", { method: "clipboard.ingest", params: JSON.stringify({ text: clipText, folder: folder || undefined }) }) as string);
      setStatus(r.noteId ? "✅ 已保存" : "❌ 保存失败"); if (r.noteId) loadFolders();
    } catch (e) { setStatus(`剪贴板错误: ${e}`); }
  };

  const resetScreenshot = () => { setScreenshotPath(null); setOcrText(""); setOcrStep("idle"); };
  const handleDeleteFolder = async (f: string) => {
    if (!f || !confirm(`确定要删除项目 "${f}" 及其所有笔记？`)) return;
    setStatus("删除中…");
    try { await invoke("backend_call", { method: "note.delete_folder", params: JSON.stringify({ folder: f }) }); setStatus("✅ 已删除"); setFolder(""); loadFolders(); }
    catch (e) { setStatus(`删除失败: ${e}`); }
  };

  const btnStyle: React.CSSProperties = { width: "100%", padding: "8px 0", border: "none", borderRadius: 6, cursor: "pointer", fontSize: 13, fontWeight: 500, transition: "opacity 0.15s", background: accent, color: "#fff" };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, height: "100%" }}>
      {/* Folder selector */}
      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
        <span style={{ fontSize: 13, color: textDim }}>📁</span>
        <SearchableFolderSelect folders={folders} value={folder} onChange={setFolder} onDeleteFolder={handleDeleteFolder} />
      </div>

      {/* Mode tabs */}
      <div style={{ display: "flex", gap: 4, background: surface, borderRadius: 6, padding: 2 }}>
        {(["text", "screenshot", "clipboard"] as Tab[]).map((t) => (
          <button key={t} onClick={() => setTab(t)} style={{ flex: 1, padding: "5px 0", border: "none", borderRadius: 5, cursor: "pointer", background: tab === t ? "#1f2937" : "transparent", color: tab === t ? "#e6edf3" : textDim, fontSize: 12, fontWeight: tab === t ? 500 : 400, transition: "all 0.15s" }}>
            {t === "text" ? "✏ 手输" : t === "screenshot" ? "📷 截图" : "📋 剪贴板"}
          </button>
        ))}
      </div>

      {/* Text mode */}
      {tab === "text" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, flex: 1 }}>
          <textarea placeholder="输入 Markdown 笔记…" value={text} onChange={(e) => setText(e.target.value)} style={{ width: "100%", flex: 1, minHeight: 100, padding: 10, border: `1px solid ${border}`, borderRadius: 6, background: inputBg, color: "#e6edf3", fontSize: 12, outline: "none", resize: "none", fontFamily: "inherit", lineHeight: 1.6 }} />
          <button onClick={handleSave} style={btnStyle}>Ctrl+Enter 保存</button>
        </div>
      )}

      {/* Screenshot mode */}
      {tab === "screenshot" && ocrStep === "idle" && (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 12, padding: "30px 0", flex: 1 }}>
          <div style={{ fontSize: 32, opacity: 0.5 }}>📷</div>
          <p style={{ fontSize: 12, color: textDim }}>截图后将询问是否提取文字</p>
          <button onClick={handleScreenshot} style={btnStyle}>截图</button>
        </div>
      )}
      {tab === "screenshot" && ocrStep === "captured" && (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8, padding: "20px 0", flex: 1 }}>
          <div style={{ fontSize: 28, marginBottom: 4 }}>✅</div>
          <p style={{ fontSize: 12, color: textDim, marginBottom: 8 }}>截图已就绪</p>
          <button onClick={handleExtractOcr} style={btnStyle}>🔍 提取文字</button>
          <button onClick={handleSaveImageOnly} style={{ ...btnStyle, background: "#21262d", color: textDim }}>📷 仅保存图片</button>
          <button onClick={resetScreenshot} style={{ background: "transparent", border: "none", cursor: "pointer", fontSize: 11, color: textDim }}>取消</button>
        </div>
      )}
      {tab === "screenshot" && ocrStep === "preview" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, flex: 1 }}>
          <div style={{ fontSize: 11, color: textDim }}>识别到的文字（可编辑）</div>
          <textarea value={ocrText} onChange={(e) => setOcrText(e.target.value)} style={{ width: "100%", flex: 1, minHeight: 80, padding: 10, border: `1px solid ${border}`, borderRadius: 6, background: inputBg, color: "#e6edf3", fontSize: 12, outline: "none", resize: "none", fontFamily: "inherit" }} autoFocus />
          <div style={{ display: "flex", gap: 4 }}>
            <button onClick={handleOcrSave} style={{ ...btnStyle, flex: 2 }}>保存（文字+图片）</button>
            <button onClick={handleSaveImageOnly} style={{ ...btnStyle, flex: 1, background: "#21262d", color: textDim }}>仅存图片</button>
          </div>
        </div>
      )}

      {/* Clipboard mode */}
      {tab === "clipboard" && (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 12, padding: "30px 0", flex: 1 }}>
          <div style={{ fontSize: 32, opacity: 0.5 }}>📋</div>
          <p style={{ fontSize: 12, color: textDim }}>从剪贴板导入文本并保存</p>
          <button onClick={handleClipboard} style={btnStyle}>导入文本</button>
        </div>
      )}

      {/* Status */}
      {status && (
        <div style={{
          fontSize: 12, color: status.startsWith("✅") || status.startsWith("已保存") ? "#2ea043" : status.startsWith("OCR") ? "#d29922" : "#f85149",
          textAlign: "center", padding: "4px 0",
        }}>
          {status}
        </div>
      )}
    </div>
  );
}
