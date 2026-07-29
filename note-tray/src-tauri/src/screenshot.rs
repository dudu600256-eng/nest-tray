// Note Tray — Screenshot capture (MVP: keybd_event + clipboard poll)

use std::path::PathBuf;
use std::process::Command;
use std::time::{Duration, Instant};

/// Spawn the system screenshot tool (Windows: Win+Shift+S).
pub fn invoke_snipping_tool() {
    Command::new("cmd")
        .args(&["/c", "start", "ms-screenclip:"])
        .spawn()
        .ok();
}

/// Poll the clipboard for an image for up to *timeout_secs* seconds.
/// The PowerShell script saves directly to *tmp_dir*.
pub fn poll_clipboard_image(
    tmp_dir: &PathBuf,
    timeout_secs: u64,
) -> Result<PathBuf, String> {
    let start = Instant::now();
    let timeout = Duration::from_secs(timeout_secs);
    let poll_interval = Duration::from_millis(200);

    while start.elapsed() < timeout {
        if let Ok(Some(path)) = save_clipboard_bitmap(tmp_dir) {
            return Ok(path);
        }
        std::thread::sleep(poll_interval);
    }
    Err("Screenshot timeout: no image in clipboard".to_string())
}

/// Use PowerShell to save clipboard bitmap directly to *tmp_dir*.
fn save_clipboard_bitmap(tmp_dir: &PathBuf) -> Result<Option<PathBuf>, String> {
    let tmp_escaped = tmp_dir.to_string_lossy().replace('\\', "\\\\");
    let script = format!(
        r#"
Add-Type -AssemblyName System.Windows.Forms
$img = [System.Windows.Forms.Clipboard]::GetImage()
if ($img -ne $null) {{
    $name = [System.IO.Path]::GetRandomFileName() + '.png'
    $path = [System.IO.Path]::Combine("{0}", $name)
    $img.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    Write-Output $path
}} else {{
    Write-Output ''
}}
"#,
        tmp_escaped
    );

    let output = Command::new("powershell")
        .args(&["-NoProfile", "-NonInteractive", "-Command", &script])
        .output()
        .map_err(|e| format!("powershell error: {}", e))?;

    let path_str = String::from_utf8(output.stdout)
        .map_err(|_| "PowerShell output is not valid UTF-8".to_string())?
        .trim()
        .to_string();

    if path_str.is_empty() {
        return Ok(None);
    }

    let path = PathBuf::from(&path_str);
    if path.exists() {
        Ok(Some(path))
    } else {
        Ok(None)
    }
}

/// Trigger screenshot flow from a blocking context.
pub fn take_screenshot(tmp_dir: &PathBuf) -> Result<PathBuf, String> {
    invoke_snipping_tool();
    poll_clipboard_image(tmp_dir, 10)
}
