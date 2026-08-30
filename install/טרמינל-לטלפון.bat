@echo off
chcp 65001 >nul
title ProfileOS - טרמינל לטלפון
color 0B
cd /d "%~dp0.."

echo.
echo   ==========================================
echo      טרמינל רצפת הייצור - חיבור מהטלפון
echo   ==========================================
echo.
echo   השאר את החלון הזה פתוח כל עוד עובדים מהטלפון.
echo.
echo   1. בטלפון - התחבר לאותו Wi-Fi של המחשב
echo   2. פתח בדפדפן את הכתובת שתופיע למטה
echo   3. הקלד את הקוד בן 6 הספרות
echo.
echo   ------------------------------------------
start "" /b python -m profileos.cli serve
timeout /t 3 >nul
python -m profileos.cli mobile pair
echo   ------------------------------------------
echo.
echo   לסגירה: סגור את החלון הזה.
pause >nul
