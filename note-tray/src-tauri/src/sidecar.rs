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
    /// 2. Sidecar binary `resources/note-backend/` (bundled)
    /// 3. Resolve from the cargo manifest dir (dev mode)
    pub fn new(kb_root: &str) -> Self {
        let python_exe = resolve_python();
        let backend_dir = resolve_backend_dir();

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
pub fn resolve_backend_dir() -> String {
    let dir = std::env::var("NOTE_BACKEND_DIR").unwrap_or_else(|_| {
        // Try: from CARGO_MANIFEST_DIR (compile-time), canonicalized
        let from_cargo = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .and_then(|p| p.join("..").join("note-backend").canonicalize().ok())
            .filter(|p| p.exists());
        // Try: from current working directory
        let from_cwd = std::env::current_dir()
            .ok()
            .and_then(|d| d.join("..").join("note-backend").canonicalize().ok())
            .filter(|p| p.exists());
        let bundled = std::path::PathBuf::from("resources").join("note-backend");

        if bundled.exists() {
            bundled.canonicalize().unwrap_or(bundled).to_string_lossy().to_string()
        } else if let Some(p) = from_cargo {
            p.to_string_lossy().to_string()
        } else if let Some(p) = from_cwd {
            p.to_string_lossy().to_string()
        } else {
            format!("{}/note-backend", std::env::current_dir().map(|d| d.to_string_lossy().to_string()).unwrap_or_default())
        }
    });
    eprintln!("[sidecar] Backend dir: {}", dir);
    dir
}
