@echo off
title ZCode 会话清理
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 python，请先安装 Python 3.8+ 并加入 PATH。
    pause
    exit /b 1
)

echo 正在启动 ZCode 会话清理 GUI...
start "" http://127.0.0.1:8765
python gui_server.py --port 8765
pause
