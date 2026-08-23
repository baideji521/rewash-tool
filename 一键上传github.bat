@echo off
chcp 65001 >nul
title 一键上传 GitHub
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0upload_github.ps1" %*
echo.
pause
