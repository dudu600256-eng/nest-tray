// Note Tray — Sidecar configuration

pub struct SidecarConfig {
    pub python_exe: String,
    pub backend_dir: String,
    pub kb_root: String,
}

impl SidecarConfig {
    /// Build a sidecar config.
    ///
    /// Python executable resolution:
    /// 1. `NOTE_PYTHON` environment variable
    /// 2. `python` / `python.exe` on PATH
    /// 3. `python3` / `python3.exe` on PATH
    /// 4. Known venv path (project-local)
    /// 5. Fallback literal `"python"` (will fail with a clear error)
    ///
    /// Backend directory:
    /// 1. `NOTE_BACKEND_DIR` environment variable
    /// 2. Source `note-backend/` directory (dev mode with Python interpreter)
    /// 3. Sidecar binary `resources/note-backend/` (bundled exe)
    pub fn new(kb_root: &str) -> Self {
        let python_exe = resolve_python();
        let is_bundled = !python_exe.ends_with("python.exe") && !python_exe.ends_with("python3.exe");
        let backend_dir = resolve_backend_dir(!is_bundled);  // prefer_source = true when using interpreter

        SidecarConfig {
            python_exe,
            backend_dir,
            kb_root: kb_root.to_string(),
        }
    }
}

/// Resolve Python executable for sidecar, with debug output.
pub fn resolve_python() -> String {
    let found = std::env::var("NOTE_PYTHON")
        .or_else(|_| {
            let venv = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .map(|p| p.join("..").join("note-backend").join(".venv").join("Scripts").join("python.exe"));
            match venv {
                Some(p) if p.exists() => Ok(p.to_string_lossy().to_string()),
                _ => Err("no project venv".to_string()),
            }
        })
        .or_else(|_| which_real_python().map(|s| s.to_string_lossy().to_string()))
        .unwrap_or_else(|_| "python".to_string());
    eprintln!("[sidecar] Python: {}", found);
    found
}

/// Like `which()` but skips the WindowsApps stub (which opens the Store).
fn which_real_python() -> Result<std::path::PathBuf, String> {
    let paths = std::env::var_os("PATH").ok_or("no PATH")?;
    for dir in std::env::split_paths(&paths) {
        // Skip WindowsApps — it contains a stub that opens the Store
        if dir.to_string_lossy().contains("WindowsApps") {
            continue;
        }
        let candidate = dir.join("python.exe");
        if candidate.exists() {
            return Ok(candidate);
        }
    }
    Err("real python not found on PATH".to_string())
}

/// Resolve backend directory path, with debug output.
///
/// * `prefer_source` — if `true` (dev mode, Python interpreter), prefer the source
///   `note-backend/` directory. If `false` (bundled exe), prefer the resources dir.
pub fn resolve_backend_dir(prefer_source: bool) -> String {
    let dir = std::env::var("NOTE_BACKEND_DIR").unwrap_or_else(|_| {
        // Try: from CARGO_MANIFEST_DIR (compile-time), canonicalized
        // Path: {manifest_dir}/../../note-backend (skip parent() once for src-tauri, once for note-tray)
        let manif = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let from_cargo = manif
            .parent()
            .and_then(|p| p.parent())
            .map(|p| p.join("note-backend"))
            .and_then(|p| p.canonicalize().ok())
            .filter(|p| p.exists());
        // Try: from current working directory
        let from_cwd = std::env::current_dir()
            .ok()
            .and_then(|d| d.join("..").join("note-backend").canonicalize().ok())
            .filter(|p| p.exists());
        let bundled = std::path::PathBuf::from("resources").join("note-backend");

        if prefer_source {
            // Dev mode (Python interpreter): use source code directory
            if let Some(p) = from_cargo {
                p.to_string_lossy().to_string()
            } else if let Some(p) = from_cwd {
                p.to_string_lossy().to_string()
            } else if bundled.exists() {
                // Fallback: bundled resources (won't have main.py but try anyway)
                bundled.canonicalize().unwrap_or(bundled).to_string_lossy().to_string()
            } else {
                format!("{}/note-backend", std::env::current_dir().map(|d| d.to_string_lossy().to_string()).unwrap_or_default())
            }
        } else {
            // Bundled exe mode: use resources directory
            if bundled.exists() {
                bundled.canonicalize().unwrap_or(bundled).to_string_lossy().to_string()
            } else if let Some(p) = from_cargo {
                p.to_string_lossy().to_string()
            } else if let Some(p) = from_cwd {
                p.to_string_lossy().to_string()
            } else {
                format!("{}/note-backend", std::env::current_dir().map(|d| d.to_string_lossy().to_string()).unwrap_or_default())
            }
        }
    });
    eprintln!("[sidecar] Backend dir: {}", dir);
    dir
}
