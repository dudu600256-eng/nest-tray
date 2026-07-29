// Note Tray — Tauri backend entry point

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod rpc;
mod screenshot;
mod sidecar;
mod tray;

use std::sync::Arc;
use tokio::sync::Mutex;
use tauri::Manager;

use tray::NoteTray;

#[tokio::main]
async fn main() {
    let config = tray::load_config();
    let note_tray = match NoteTray::new(config) {
        Ok(nt) => Arc::new(Mutex::new(nt)),
        Err(e) => {
            eprintln!("Failed to start NoteTray: {}", e);
            return;
        }
    };

    tauri::Builder::default()
        .manage(note_tray.clone())
        .invoke_handler(tauri::generate_handler![
            backend_call,
            take_screenshot,
            open_logs,
            save_config_cmd,
        ])
        .setup(|app| {
            use tauri::menu::{MenuBuilder, MenuItemBuilder};

            let open = MenuItemBuilder::with_id("open", "打开笔记").build(app)?;
            let search = MenuItemBuilder::with_id("search", "搜索笔记").build(app)?;
            let quit = MenuItemBuilder::with_id("quit", "退出").build(app)?;
            let menu = MenuBuilder::new(app)
                .item(&open).item(&search).separator().item(&quit)
                .build()?;

            // Load tray icon from PNG
            let icon_data = include_bytes!("../icons/32x32.png");
            let icon_img = image::load_from_memory(icon_data)
                .map_err(|e| e.to_string())?.to_rgba8();
            let (w, h) = icon_img.dimensions();
            let raw_rgba = icon_img.into_raw();
            let icon = tauri::image::Image::new(&raw_rgba, w, h);
            eprintln!("[tray] icon loaded: {}x{}", w, h);

            tauri::tray::TrayIconBuilder::new()
                .icon(icon)
                .menu(&menu)
                .tooltip("衔泥 NestTray")
                .on_menu_event(move |app, event| {
                    match event.id().as_ref() {
                        "open" => { if let Some(w) = app.get_webview_window("main") { w.show().ok(); w.set_focus().ok(); } }
                        "search" => { if let Some(w) = app.get_webview_window("main") { w.show().ok(); w.set_focus().ok(); } }
                        "quit" => app.exit(0),
                        _ => {}
                    }
                })
                .build(app)?;

            // Close button → hide to tray, not quit
            if let Some(window) = app.get_webview_window("main") {
                let win = window.clone();
                window.on_window_event(move |event| {
                    if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        let _ = win.hide();
                    }
                });
            }

            Ok(())

        })
        .run(tauri::generate_context!())
        .expect("error while running note-tray");
}

#[tauri::command]
async fn backend_call(
    method: String,
    params: String,
    note_tray: tauri::State<'_, Arc<Mutex<NoteTray>>>,
) -> Result<String, String> {
    let nt = note_tray.lock().await;
    let p: serde_json::Value = serde_json::from_str(&params).map_err(|e| e.to_string())?;
    let timeout = if method.starts_with("ocr.") { 120 } else { 10 };
    eprintln!("[tauri] RPC call: {} timeout={}", method, timeout);
    match nt.rpc.call(&method, p, timeout) {
        Ok(result) => {
            eprintln!("[tauri] RPC ok: {} -> {}", method, serde_json::to_string(&result).unwrap_or_default().chars().take(60).collect::<String>());
            serde_json::to_string(&result).map_err(|e| e.to_string())
        }
        Err(e) => {
            eprintln!("[tauri] RPC error: {} -> {}", method, e);
            Err(e)
        }
    }
}

#[tauri::command]
async fn take_screenshot(
    note_tray: tauri::State<'_, Arc<Mutex<NoteTray>>>,
    app: tauri::AppHandle,
) -> Result<String, String> {
    // Hide main window so it doesn't appear in screenshot
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.hide();
    }
    // Small delay for window to hide
    tokio::time::sleep(tokio::time::Duration::from_millis(300)).await;

    let tmp_dir = dirs::data_dir()
        .unwrap_or_else(|| std::path::PathBuf::from("."))
        .join("note-tray")
        .join("tmp");
    std::fs::create_dir_all(&tmp_dir).map_err(|e| format!("create tmp: {}", e))?;
    let path = screenshot::take_screenshot(&tmp_dir)?;

    // Restore window
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
    Ok(path.to_string_lossy().to_string())
}

#[tauri::command]
async fn open_logs() -> Result<(), String> {
    // Use Roaming dir (same as Python's %APPDATA%)
    let logs_dir = dirs::data_dir()
        .unwrap_or_else(|| std::path::PathBuf::from("."))
        .join("note-tray")
        .join("logs");
    // Ensure directory exists
    std::fs::create_dir_all(&logs_dir).map_err(|e| format!("create logs dir: {}", e))?;
    // Use explorer.exe directly on Windows
    std::process::Command::new("explorer")
        .arg(&logs_dir)
        .spawn()
        .map_err(|e| format!("open logs dir: {}", e))?;
    Ok(())
}

#[tauri::command]
async fn save_config_cmd(kb_root: String, hotkey: String, last_folder: String) -> Result<(), String> {
    let cfg = tray::TrayConfig { kb_root, hotkey, last_folder };
    tray::save_config(&cfg);
    Ok(())
}
