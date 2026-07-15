@echo off
chcp 65001 > nul
title 华康洁净物料清单匹配工具 V0.0.2
echo ==========================================================
echo   正在启动 华康洁净物料清单匹配工具 V0.0.2...
echo ==========================================================

:: 检查 Python 环境
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python，请确保您的系统已安装 Python 并已将其配置到环境变量 PATH 中！
    pause
    exit /b 1
)

:: 启动浏览器打开服务页面
echo 正在为您打开网页浏览器...
start http://127.0.0.1:8000

:: 运行 Python 后端服务
python -m app.server
pause
