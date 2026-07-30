// Note Tray — JSON-RPC 2.0 line protocol client (persistent sidecar)

use std::io::{BufRead, BufReader, Write};
use std::process::{ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::thread;

use serde_json::Value;

pub struct RpcClient {
    stdin: Arc<Mutex<ChildStdin>>,
    pending: Arc<Mutex<std::collections::HashMap<String, mpsc::Sender<Value>>>>,
    next_id: AtomicU64,
    alive: Arc<AtomicBool>,
}

impl RpcClient {
    pub fn spawn(python_exe: &str, backend_dir: &str, kb_root: &str) -> Result<Self, String> {
        // If python_exe is a standalone bundled exe (not python interpreter),
        // don't pass main.py as argument
        let is_bundled = !python_exe.ends_with("python.exe") && !python_exe.ends_with("python3.exe");

        let mut cmd = Command::new(python_exe);
        if !is_bundled {
            cmd.arg("main.py");
        }
        let mut child = cmd
            .current_dir(backend_dir)
            .env("NOTE_KB_ROOT", kb_root)
            .env("PYTHONIOENCODING", "utf-8")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| format!("sidecar spawn: {}", e))?;

        let stdin = child.stdin.take().ok_or("no stdin")?;
        let stdout = child.stdout.take().ok_or("no stdout")?;
        let stderr = child.stderr.take().ok_or("no stderr")?;

        // Read hello handshake
        let mut reader = BufReader::new(stdout);
        let mut hello = String::new();
        reader.read_line(&mut hello).map_err(|e| format!("read hello: {}", e))?;
        if !hello.contains("backend.hello") {
            return Err(format!("unexpected hello: {}", hello));
        }
        eprintln!("[sidecar] Handshake OK");

        let alive = Arc::new(AtomicBool::new(true));

        // Spawn stderr reader — detect process exit on EOF
        let alive_stderr = alive.clone();
        let child_for_wait = child.try_wait().ok().flatten(); // Check if already exited
        thread::spawn(move || {
            for line in BufReader::new(stderr).lines() {
                if let Ok(l) = line {
                    eprintln!("[sidecar] {}", l);
                }
            }
            // stderr EOF → process died
            alive_stderr.store(false, Ordering::SeqCst);
            eprintln!("[sidecar] Python process exited (stderr EOF)");
        });

        let pending: Arc<Mutex<std::collections::HashMap<String, mpsc::Sender<Value>>>> =
            Arc::new(Mutex::new(std::collections::HashMap::new()));

        // Spawn stdout reader thread — also detects process exit
        let pending_clone = pending.clone();
        let alive_stdout = alive.clone();
        thread::spawn(move || {
            for line in reader.lines() {
                if let Ok(line) = &line {
                    let preview: String = line.chars().take(80).collect();
                    eprintln!("[rpc] stdout line: {}...", preview);
                }
                if let Ok(line) = line {
                    if let Ok(val) = serde_json::from_str::<Value>(&line) {
                        if let Some(id) = val.get("id").and_then(|v| v.as_str()) {
                            let map = pending_clone.lock().unwrap();
                            if let Some(sender) = map.get(id) {
                                let _ = sender.send(val.clone());
                            }
                        } else if let Some(method) = val.get("method").and_then(|v| v.as_str()) {
                            match method {
                                "event.fatal" => eprintln!("[sidecar] FATAL"),
                                "event.ocr_ready" => eprintln!("[sidecar] OCR ready"),
                                "event.index.rebuilt" => eprintln!("[sidecar] Index rebuilt"),
                                _ => {}
                            }
                        }
                    }
                }
            }
            // stdout EOF → process died
            alive_stdout.store(false, Ordering::SeqCst);
        });

        Ok(RpcClient {
            stdin: Arc::new(Mutex::new(stdin)),
            pending,
            next_id: AtomicU64::new(1),
            alive,
        })
    }

    pub fn call(&self, method: &str, params: Value, timeout_secs: u64) -> Result<Value, String> {
        if !self.alive.load(Ordering::SeqCst) {
            return Err("Python 后端已停止".to_string());
        }
        let id = self.next_id.fetch_add(1, Ordering::SeqCst).to_string();
        let request = serde_json::json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params,
        });

        let (tx, rx) = mpsc::channel();
        {
            let mut map = self.pending.lock().unwrap();
            map.insert(id.clone(), tx);
        }

        {
            let mut stdin = self.stdin.lock().unwrap();
            let line = serde_json::to_string(&request).map_err(|e| e.to_string())?;
            eprintln!("[rpc] sending to stdin: {}", &line[..line.len().min(120)]);
            use std::io::Write;
            stdin.write_all(line.as_bytes()).map_err(|e| format!("stdin write: {}", e))?;
            stdin.write_all(b"\n").map_err(|e| format!("stdin write newline: {}", e))?;
            stdin.flush().map_err(|e| format!("stdin flush: {}", e))?;
            eprintln!("[rpc] sent, waiting for response...");
        }

        let val = rx
            .recv_timeout(std::time::Duration::from_secs(timeout_secs))
            .map_err(|_| "RPC timeout or cancelled".to_string())?;

        eprintln!("[rpc] got response for id={}", id);
        self.pending.lock().unwrap().remove(&id);

        if let Some(error) = val.get("error") {
            let msg = error.get("message").and_then(|v| v.as_str()).unwrap_or("RPC error");
            return Err(msg.to_string());
        }

        Ok(val.get("result").cloned().unwrap_or(Value::Null))
    }
}
