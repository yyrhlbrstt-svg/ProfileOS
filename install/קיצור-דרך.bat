@echo off
rem Creates a desktop shortcut to the launcher, with the app's own icon.
set TARGET=%~dp0..\ProfileOS.bat
set SHORTCUT=%USERPROFILE%\Desktop\ProfileOS.lnk
powershell -NoProfile -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%SHORTCUT%');" ^
  "$s.TargetPath='%TARGET%';" ^
  "$s.WorkingDirectory='%~dp0..';" ^
  "$s.Description='ProfileOS - מערכת הנדסית לאלומיניום';" ^
  "$s.Save()"
