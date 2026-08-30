@echo off
cd /d %USERPROFILE%\Desktop
echo Checking for updates...
curl -L -o profileos_update.zip https://github.com/yyrhlbrstt-svg/ProfileOS/archive/refs/heads/claude/aluminum-cad-cam-system-dxfni8.zip
if exist profileos_update.zip (
    if exist ProfileOS-claude-aluminum-cad-cam-system-dxfni8 rmdir /s /q ProfileOS-claude-aluminum-cad-cam-system-dxfni8
    tar -xf profileos_update.zip
    del profileos_update.zip
)
cd ProfileOS-claude-aluminum-cad-cam-system-dxfni8
pip install -e . --quiet
python -m profileos.cli ui
