@echo off
REM Note Tray — Build helper (Windows)
REM
REM Prerequisites:
REM   1. MSYS2 installed at C:\msys64
REM   2. MinGW-w64: pacman -S mingw-w64-x86_64-gcc
REM   3. Rust GNU toolchain: rustup default stable-x86_64-pc-windows-gnu
REM
REM The .cargo/config.toml handles tool paths automatically.
REM This script just runs cargo build with the right working directory.

cd /d "%~dp0src-tauri"
echo Building 衔泥 NestTray...
cargo build %*
if %ERRORLEVEL% EQU 0 (
    echo.
    echo Build successful!
    echo Binary: %CD%\target\debug\nest-tray.exe
) else (
    echo.
    echo Build failed. Check errors above.
)
pause
