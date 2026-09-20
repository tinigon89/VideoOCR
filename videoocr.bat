@echo off
REM Chay bang dong lenh. Vi du:
REM   videoocr.bat "D:\Phim" --language zh
REM   videoocr.bat "D:\Phim" --language en --model distil-large-v3
cd /d "%~dp0"
chcp 65001 >nul

if not exist ".venv\Scripts\python.exe" (
    echo Chua cai dat. Hay chay setup.bat truoc.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m videoocr %*
