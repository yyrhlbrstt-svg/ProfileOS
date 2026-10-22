@echo off
chcp 65001 >nul
title ProfileOS
cd /d "%~dp0"

rem The window stays open on purpose. A launcher that hides its own console
rem also hides the one line that says why nothing happened, and a shop with a
rem silent icon has no way to tell us what went wrong.
rem
rem launcher.py puts a downloaded, tested update in place first (after a
rem backup), then starts the program, and puts the old version back by itself
rem if the new one does not open.
rem
rem Everything runs inside one block: cmd reads a block whole before it runs
rem it, so even if this file is replaced while the program is open, the copy
rem that is running is never read half-old, half-new.
(
    python launcher.py
    if errorlevel 1 (
        color 0C
        echo.
        echo   התוכנה לא נפתחה.
        echo   צלם את המסך הזה ושלח — השורות למעלה אומרות מה חסר.
        echo.
        pause
    )
    exit /b
)
