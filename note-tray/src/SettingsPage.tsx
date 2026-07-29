import { invoke } from "@tauri-apps/api/core";
import { useState, useEffect } from "react";

const accent = "#3b82f6";
const border = "#30363d";
const textDim = "#8b949e";
const inputBg = "#0d1117";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ fontSize: 11, fontWeight: 600, color: textDim, marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.5px" }}>{title}</div>
      {children}
    </div>
  );
}

export default function SettingsPage() {
  const [kbRoot, setKbRoot] = useState("");
  const [currentKbRoot, setCurrentKbRoot] = useState("(读取中…)");
  const [rebuilding, setRebuilding] = useState(false);
  const [status, setStatus] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const resp = JSON.parse(await invoke("backend_call", { method: "system.status", params: "{}" }) as string);
        const path = resp.rootPath || "(未设置)";
        setCurrentKbRoot(path); setKbRoot(path);
      } catch { setCurrentKbRoot("(后端未连接)"); }
    })();
  }, []);

  const handleSaveKbRoot = async () => {
    if (!kbRoot.trim()) return;
    setStatus("保存中…");
    try { await invoke("save_config_cmd", { kbRoot, hotkey: "", lastFolder: "" }); setStatus("✅ 已保存，重启后生效"); }
    catch (e) { setStatus(`保存失败: ${e}`); }
  };

  const handleRebuild = async () => {
    setRebuilding(true); setStatus("重建中…");
    try { await invoke("backend_call", { method: "search.rebuild", params: "{}" }); setStatus("✅ 索引重建完成"); }
    catch (e) { setStatus(`❌ 重建失败: ${e}`); } finally { setRebuilding(false); }
  };

  const handleOpenLogs = async () => {
    try { await invoke("open_logs"); } catch (e) { setStatus(`打开日志失败: ${e}`); }
  };

  const btnBase: React.CSSProperties = { padding: "7px 14px", border: "none", borderRadius: 6, cursor: "pointer", fontSize: 12, fontWeight: 500, background: accent, color: "#fff" };

  return (
    <div style={{ height: "100%", overflowY: "auto" }}>
      <Section title="知识库路径">
        <div style={{ fontSize: 11, color: textDim, marginBottom: 4 }}>当前: {currentKbRoot}</div>
        <div style={{ display: "flex", gap: 6 }}>
          <input value={kbRoot} onChange={(e) => setKbRoot(e.target.value)} placeholder="D:/notes" style={{ flex: 1, padding: "7px 10px", border: `1px solid ${border}`, borderRadius: 6, background: inputBg, color: "#e6edf3", fontSize: 12, outline: "none" }} />
          <button onClick={handleSaveKbRoot} style={btnBase}>保存</button>
        </div>
        <div style={{ fontSize: 10, color: textDim, marginTop: 4 }}>修改后需重启应用</div>
      </Section>

      <Section title="索引管理">
        <button onClick={handleRebuild} disabled={rebuilding} style={{ ...btnBase, width: "100%", background: rebuilding ? "#21262d" : accent }}>{rebuilding ? "重建中…" : "重建索引"}</button>
      </Section>

      <Section title="日志">
        <button onClick={handleOpenLogs} style={{ ...btnBase, width: "100%", background: "#21262d", color: textDim }}>打开日志目录</button>
      </Section>

      {status && <div style={{ fontSize: 12, color: "#2ea043", marginBottom: 8 }}>{status}</div>}

      <div style={{ fontSize: 10, color: "#6e7681", marginTop: 16, textAlign: "center" }}>
        衔泥 NestTray v0.1.0
      </div>
    </div>
  );
}
