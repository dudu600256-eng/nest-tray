fn main() {
    // Ensure MinGW tools (windres, dlltool) are findable during build
    let mingw_bin = "C:\\msys64\\mingw64\\bin";
    if let Ok(path) = std::env::var("PATH") {
        std::env::set_var("PATH", format!("{};{}", mingw_bin, path));
    }
    tauri_build::build();
}
