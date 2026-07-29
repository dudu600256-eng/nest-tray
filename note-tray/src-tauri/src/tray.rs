// Note Tray — System tray and application state

use serde::{Deserialize, Serialize};

use crate::rpc::RpcClient;
use crate::sidecar::SidecarConfig;
use crate::screenshot;

#[derive(Clone, Serialize, Deserialize)]
pub struct TrayConfig {
    pub kb_root: String,
    pub hotkey: String,
    pub last_folder: String,
}

pub struct NoteTray {
    pub rpc: RpcClient,
    pub config: TrayConfig,
}

impl NoteTray {
    pub fn new(config: TrayConfig) -> Result<Self, String> {
        let sc = SidecarConfig::new(&config.kb_root);
        let rpc = RpcClient::spawn(&sc.python_exe, &sc.backend_dir, &sc.kb_root)?;
        Ok(NoteTray {
            rpc,
            config,
        })
    }

    pub fn handle_screenshot(&self) -> Result<(), String> {
        let tmp_dir = dirs::data_dir()
            .unwrap_or_else(|| std::path::PathBuf::from("."))
            .join("note-tray")
            .join("tmp");
        std::fs::create_dir_all(&tmp_dir).ok();
        let path = screenshot::take_screenshot(&tmp_dir)?;
        self.rpc.call(
            "ocr.store",
            serde_json::json!({
                "imagePath": path,
                "folder": self.config.last_folder,
                "mode": "both",
            }),
            60,
        )?;
        Ok(())
    }

    pub fn save_last_folder(&mut self, folder: String) {
        self.config.last_folder = folder.clone();
        save_config(&self.config);
    }
}

pub fn load_config() -> TrayConfig {
    let path = config_path();
    if let Ok(content) = std::fs::read_to_string(&path) {
        if let Ok(cfg) = serde_json::from_str::<TrayConfig>(&content) {
            return cfg;
        }
    }
    TrayConfig {
        kb_root: dirs::document_dir()
            .unwrap_or_else(|| std::path::PathBuf::from("."))
            .join("notes")
            .to_string_lossy()
            .to_string(),
        hotkey: "Ctrl+Shift+N".to_string(),
        last_folder: String::new(),
    }
}

pub fn save_config(config: &TrayConfig) {
    let path = config_path();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).ok();
    }
    if let Ok(content) = serde_json::to_string_pretty(config) {
        std::fs::write(path, content).ok();
    }
}

fn config_path() -> std::path::PathBuf {
    let base = dirs::data_dir().unwrap_or_else(|| std::path::PathBuf::from("."));
    base.join("note-tray").join("config.json")
}
