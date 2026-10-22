@echo off
chcp 65001 >nul
rem First installation on a new computer, in one double-click: downloads the
rem latest *published* version (one that passed every test), installs it on
rem the Desktop and starts it. On a computer that already has ProfileOS it only
rem starts it — the program then keeps itself up to date, and nothing here
rem ever deletes a folder.

set TARGET=%USERPROFILE%\Desktop\ProfileOS
if exist "%TARGET%\launcher.py" goto run

echo   מוריד את הגרסה האחרונה של ProfileOS...
mkdir "%TARGET%" 2>nul
powershell -NoProfile -Command ^
  "$r = Invoke-RestMethod 'https://api.github.com/repos/yyrhlbrstt-svg/ProfileOS/releases/latest';" ^
  "$a = $r.assets | Where-Object { $_.name -like 'profileos-*.zip' } | Select-Object -First 1;" ^
  "Invoke-WebRequest $a.browser_download_url -OutFile \"$env:TEMP\profileos.zip\";" ^
  "Expand-Archive \"$env:TEMP\profileos.zip\" -DestinationPath '%TARGET%' -Force"
if not exist "%TARGET%\launcher.py" (
    echo   ההורדה נכשלה. בדקו את חיבור האינטרנט ונסו שוב.
    pause
    exit /b 1
)
call "%TARGET%\install\התקנה.bat"

:run
cd /d "%TARGET%"
call ProfileOS.bat
