import { invoke } from "@tauri-apps/api/core";
import { useState, useRef } from "react";

const surface = "#161b22";
const border = "#30363d";
const textDim = "#8b949e";
const inputBg = "#0d1117";

interface SearchResult {
  path: string; title: string; snippet: string; score: number; noteId?: string;
}

export default function SearchWindow() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [status, setStatus] = useState("");
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  const doSearch = async (q: string) => {
    if (!q.trim()) { setResults([]); return; }
    setStatus("搜索中…");
    try {
      const resp = JSON.parse(await invoke("backend_call", { method: "search.query", params: JSON.stringify({ q, limit: 20 }) }) as string);
      setResults(resp.results || []);
      setStatus(`共 ${resp.totalHits || 0} 条结果`);
    } catch (e) { setStatus(`搜索失败: ${e}`); }
  };

  const handleInput = (value: string) => {
    setQuery(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => doSearch(value), 300);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, height: "100%" }}>
      <input placeholder="搜索笔记…（至少 3 字）" value={query} onChange={(e) => handleInput(e.target.value)} style={{ width: "100%", padding: "8px 10px", border: `1px solid ${border}`, borderRadius: 6, background: inputBg, color: "#e6edf3", fontSize: 12, outline: "none" }} autoFocus />
      {status && <div style={{ fontSize: 11, color: textDim }}>{status}</div>}
      <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: 6 }}>
        {results.length === 0 && query && !status.includes("搜索中") && (
          <div style={{ fontSize: 12, color: textDim, textAlign: "center", padding: 20 }}>无结果</div>
        )}
        {results.map((r, i) => (
          <div key={i} style={{ padding: "8px 10px", borderRadius: 6, background: surface, border: `1px solid transparent`, cursor: "pointer" }}
            onMouseEnter={(e) => e.currentTarget.style.borderColor = border}
            onMouseLeave={(e) => e.currentTarget.style.borderColor = "transparent"}
          >
            <div style={{ fontSize: 12, fontWeight: 500, color: "#e6edf3", marginBottom: 2 }}>{r.title}</div>
            <div style={{ fontSize: 11, color: textDim, lineHeight: 1.5, marginBottom: 2 }} dangerouslySetInnerHTML={{ __html: r.snippet || "" }} />
            <div style={{ fontSize: 10, color: "#6e7681" }}>{r.path}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
