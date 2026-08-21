@echo off
chcp 65001 >nul
title ProfileOS
cd /d "%~dp0"

rem The window stays open on purpose. A launcher that hides its own console
rem also hides the one line that says why nothing happened, and a shop with a
rem silent icon has no way to tell us what went wrong.

python -c "from profileos.ui.app import run; raise SystemExit(run())"
if errorlevel 1 (
    color 0C
    echo.
    echo   התוכנה לא נפתחה.
    echo   צלם את המסך הזה ושלח — השורות למעלה אומרות מה חסר.
    echo.
    pause
)
