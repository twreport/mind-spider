@echo off
chcp 65001 >nul
title MindSpider Scheduler

echo ============================================
echo   MindSpider 数据采集调度器
echo ============================================
echo.

:: 检查 MongoDB 是否运行
docker ps --filter "name=mongo" --format "{{.Status}}" 2>nul | findstr "Up" >nul
if errorlevel 1 (
    echo [!] MongoDB 未运行，正在启动...
    docker start mongo
    timeout /t 3 /nobreak >nul
) else (
    echo [OK] MongoDB 已运行
)

echo.
echo 启动调度器...
echo 按 Ctrl+C 停止
echo.

cd /d %~dp0
uv run python start_scheduler.py %*
