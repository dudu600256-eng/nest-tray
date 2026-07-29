@echo off
set PATH=C:\msys64\mingw64\bin;%USERPROFILE%\.cargo\bin;%LOCALAPPDATA%\Microsoft\WindowsApps;%PATH%
cd /d "%~dp0"
echo [tauri-dev] Starting 衔泥 NestTray...
npx tauri dev %*
