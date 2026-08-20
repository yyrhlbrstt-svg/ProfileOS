@echo off
chcp 65001 >nul
title ProfileOS
cd /d "%~dp0"
start "" /b pythonw -m profileos.cli ui 2>nul || python -m profileos.cli ui
