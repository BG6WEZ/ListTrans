@echo off
chcp 65001 > nul
title 华康洁净物料清单匹配工具 V0.0.6
echo ==========================================================
echo   正在启动 华康洁净物料清单匹配工具 V0.0.6...
echo ==========================================================

:: 设置 PYTHONPATH 环境变量，确保本地 python 能够正确加载 app 模块
set PYTHONPATH=%~dp0

:: 检查 Python 环境
if exist "%~dp0python_local\python.exe" (
    set "PY_CMD=%~dp0python_local\python.exe"
) else (
    set "PY_CMD=python"
)

:: 启动浏览器打开服务页面
echo 正在为您打开网页浏览器...
start http://127.0.0.1:18080

:: 运行 Python 后端服务
%PY_CMD% -m app.server
pause
