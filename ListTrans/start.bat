@echo off
chcp 65001 > nul
title 华康洁净物料清单匹配工具 V0.0.2
echo ==========================================================
echo   正在启动 华康洁净物料清单匹配工具 V0.0.2...
echo ==========================================================

:: 设置 PYTHONPATH 环境变量，确保本地 python 能够正确加载 app 模块
set PYTHONPATH=%~dp0

:: 检查本地 Python 环境
if not exist "%~dp0python_local\python.exe" (
    echo [错误] 未在本地检测到 python_local\python.exe，请确保开发环境已就绪！
    pause
    exit /b 1
)

:: 启动浏览器打开服务页面
echo 正在为您打开网页浏览器...
start http://127.0.0.1:8000

:: 运行 Python 后端服务
"%~dp0python_local\python.exe" -m app.server
pause
