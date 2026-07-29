import { useState, useEffect } from "react";
import CaptureWindow from "./CaptureWindow";
import SearchWindow from "./SearchWindow";
import SettingsPage from "./SettingsPage";

type View = "capture" | "search" | "settings";

const accent = "#3b82f6";
const border = "#30363d";
const textDim = "#8b949e";

function App() {
  const [view, setView] = useState<View>("capture");
  const [backendStatus, setBackendStatus] = useState<"connecting" | "ready" | "error">("connecting");

  useEffect(() => {
    const timer = setTimeout(() => setBackendStatus("ready"), 2000);
    return () => clearTimeout(timer);
  }, []);

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 14px 0" }}>
        <div style={{ width: 22, height: 22, borderRadius: 5, overflow: "hidden", flexShrink: 0 }}>
          <img src="/icon.png" style={{ width: 22, height: 22, display: "block" }} alt="NestTray" />
        </div>
        <span style={{ fontWeight: 600, fontSize: 14, color: "#e6edf3" }}>衔泥</span>
        <span style={{ fontSize: 11, color: textDim, marginLeft: -4 }}>NestTray</span>
        <div style={{ flex: 1 }} />
        <div style={{ fontSize: 10, color: backendStatus === "ready" ? "#2ea043" : "#d29922", display: "flex", alignItems: "center", gap: 4 }}>
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: backendStatus === "ready" ? "#2ea043" : "#d29922", display: "inline-block" }} />
          {backendStatus === "connecting" ? "连接中" : backendStatus === "ready" ? "已就绪" : "异常"}
        </div>
      </div>

      {/* Nav tabs */}
      <div style={{ display: "flex", gap: 2, padding: "10px 14px 0", borderBottom: `1px solid ${border}` }}>
        {( [
          { key: "capture" as View, label: "📝 捕获" },
          { key: "search" as View, label: "🔍 搜索" },
          { key: "settings" as View, label: "⚙ 设置" },
        ]).map((t) => (
          <button
            key={t.key}
            onClick={() => setView(t.key)}
            style={{
              padding: "6px 14px", border: "none", cursor: "pointer",
              background: "transparent", color: view === t.key ? "#e6edf3" : textDim,
              borderBottom: view === t.key ? `2px solid ${accent}` : "2px solid transparent",
              fontSize: 12, fontWeight: view === t.key ? 600 : 400,
              marginBottom: -1, transition: "color 0.15s",
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div style={{ flex: 1, padding: "10px 14px", overflow: "hidden" }}>
        <div style={{ height: "100%", overflow: "hidden" }}>
          {view === "capture" && <CaptureWindow />}
          {view === "search" && <SearchWindow />}
          {view === "settings" && <SettingsPage />}
        </div>
      </div>
    </div>
  );
}

export default App;
