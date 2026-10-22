@echo off
goto start
:: This file may be put in place of an older copy while that copy is still
:: running (the program offers to, once). cmd then reads on from where the
:: old copy ended: whatever offset that is, it lands in the empty lines
:: below and reaches "exit /b" — never a stray half-command.
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
::                                                                                                  
exit /b

:start
chcp 65001 >nul
rem First installation on a new computer, in one double-click: downloads the
rem latest *published* version (one that passed every test), checks its
rem fingerprint, installs it on the Desktop and starts it. On a computer that
rem already has it, it only starts it — the program keeps itself up to date,
rem and nothing here ever deletes a folder.

set "TARGET=%USERPROFILE%\Desktop\ProfileOS"
if exist "%TARGET%\launcher.py" goto run

echo   מוריד את הגרסה האחרונה של ProfileOS...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "[Net.ServicePointManager]::SecurityProtocol = 'Tls12';" ^
  "$r = Invoke-RestMethod 'https://api.github.com/repos/yyrhlbrstt-svg/ProfileOS/releases/latest';" ^
  "$j = Join-Path $env:TEMP 'profileos-release.json';" ^
  "Invoke-WebRequest ($r.assets | Where-Object name -eq 'release.json').browser_download_url -OutFile $j -UseBasicParsing;" ^
  "$m = Get-Content $j -Raw -Encoding UTF8 | ConvertFrom-Json;" ^
  "$f = Join-Path $env:TEMP $m.zip;" ^
  "Invoke-WebRequest ($r.assets | Where-Object name -eq $m.zip).browser_download_url -OutFile $f -UseBasicParsing;" ^
  "if ((Get-FileHash $f -Algorithm SHA256).Hash.ToLower() -ne $m.sha256) { Remove-Item $f; throw 'fingerprint mismatch' };" ^
  "Expand-Archive $f -DestinationPath $env:TARGET -Force; Remove-Item $f, $j"
if not exist "%TARGET%\launcher.py" (
    echo   ההורדה נכשלה, או שהקובץ שירד לא תאם את טביעת האצבע שפורסמה.
    echo   בדקו את חיבור האינטרנט ונסו שוב.
    pause
    exit /b 1
)
call "%TARGET%\install\התקנה.bat"

:run
cd /d "%TARGET%"
call ProfileOS.bat & exit /b
