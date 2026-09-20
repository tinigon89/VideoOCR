@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo Chua cai dat. Hay chay setup.bat truoc.
    pause
    exit /b 1
)

REM pythonw mo cua so khong kem console den phia sau.
start "" ".venv\Scripts\pythonw.exe" -m videoocr --gui
