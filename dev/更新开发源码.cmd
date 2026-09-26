@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
title Jev 开发源码更新

echo.
echo ========================================
echo   JevChat-Windows 开发版源码更新
echo ========================================
echo.

where powershell.exe >nul 2>nul
if errorlevel 1 (
    echo [失败] 没有找到 Windows PowerShell。
    echo.
    pause
    exit /b 1
)

if not exist "%~dp0dev_update.ps1" (
    echo [失败] 缺少 dev_update.ps1
    echo 请确认它和“更新开发源码.cmd”放在同一个文件夹。
    echo.
    pause
    exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev_update.ps1"
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo [完成] 源码已经更新，可以重新打开 JevChat-Dev.exe。
) else (
    echo [失败] 更新没有完成，错误代码：%RC%
)
echo.
pause
exit /b %RC%
