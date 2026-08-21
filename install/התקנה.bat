@echo off
chcp 65001 >nul
title ProfileOS - התקנה
color 0E

echo.
echo   ==========================================
echo      ProfileOS - התקנה
echo      דאדי בע"מ
echo   ==========================================
echo.

cd /d "%~dp0.."

echo   [1/3] בודק Python...
python --version >nul 2>&1
if errorlevel 1 (
    color 0C
    echo.
    echo   שגיאה: Python לא מותקן, או שלא סומן "Add to PATH" בהתקנה.
    echo.
    echo   מה לעשות:
    echo     1. גלוש אל  https://www.python.org/downloads
    echo     2. הורד והרץ את הקובץ
    echo     3. סמן את התיבה  "Add python.exe to PATH"  לפני Install
    echo     4. הרץ את הקובץ הזה שוב
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version') do echo         נמצא %%v

echo   [2/3] מתקין את התוכנה ואת כל הרכיבים...
echo         (זה לוקח כמה דקות בפעם הראשונה - אל תסגור את החלון)
python -m pip install --upgrade pip --quiet
python -m pip install -e ".[all]" --quiet
if errorlevel 1 (
    color 0C
    echo.
    echo   ההתקנה נכשלה. בדוק את חיבור האינטרנט והרץ שוב.
    echo   אם זה חוזר - צלם את המסך הזה ושלח.
    echo.
    pause
    exit /b 1
)

echo   [3/3] מכין נתוני התחלה ויוצר קיצור דרך...
python -m profileos.cli seed --quiet
call "%~dp0קיצור-דרך.bat" >nul 2>&1

color 0A
echo.
echo   ==========================================
echo      ההתקנה הסתיימה
echo   ==========================================
echo.
echo   להפעלה: לחץ פעמיים על  "ProfileOS.bat"  בתיקייה הראשית,
echo   או על קיצור הדרך שנוצר על שולחן העבודה.
echo.
pause
