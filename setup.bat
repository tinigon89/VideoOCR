@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==========================================
echo   VideoOCR - Cai dat moi truong
echo ==========================================
echo.

REM ---------- 1. Kiem tra Python ----------
where python >nul 2>nul
if errorlevel 1 (
    echo [X] Khong tim thay Python trong PATH.
    echo.
    echo     Tai Python 3.10 tro len tai: https://www.python.org/downloads/
    echo     Khi cai nho tich o "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" 2>nul
if errorlevel 1 (
    echo [X] Python qua cu. Can tu 3.9 tro len.
    python --version
    pause
    exit /b 1
)

for /f "delims=" %%V in ('python --version') do echo [OK] %%V

REM ---------- 2. Tao moi truong ao ----------
if not exist ".venv\Scripts\python.exe" (
    echo [..] Dang tao moi truong ao .venv
    python -m venv .venv
    if errorlevel 1 (
        echo [X] Tao venv that bai.
        pause
        exit /b 1
    )
)
echo [OK] Moi truong ao san sang.

set "PY=.venv\Scripts\python.exe"

REM ---------- 3. Cai thu vien ----------
echo.
echo [..] Dang cai thu vien. Lan dau se tai khoang 1-2 GB, hay kien nhan.
echo.
"%PY%" -m pip install --upgrade pip --quiet
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [X] Cai thu vien that bai. Kiem tra ket noi mang roi chay lai.
    pause
    exit /b 1
)
echo [OK] Da cai xong thu vien.

REM ---------- 4. ffmpeg ----------
echo.
set "NEED_FFMPEG=1"
if exist "bin\ffmpeg.exe" if exist "bin\ffprobe.exe" set "NEED_FFMPEG=0"
if "%NEED_FFMPEG%"=="1" (
    where ffmpeg >nul 2>nul
    if not errorlevel 1 (
        where ffprobe >nul 2>nul
        if not errorlevel 1 set "NEED_FFMPEG=0"
    )
)

if "%NEED_FFMPEG%"=="0" (
    echo [OK] ffmpeg da san sang.
) else (
    echo [..] Chua co ffmpeg, dang tai ve thu muc bin\
    call :get_ffmpeg
    if errorlevel 1 (
        echo.
        echo [!] Tai ffmpeg that bai. Ban co the tu tai tai https://www.gyan.dev/ffmpeg/builds/
        echo     roi chep ffmpeg.exe va ffprobe.exe vao thu muc bin\ canh file nay.
        echo.
    ) else (
        echo [OK] Da cai ffmpeg vao bin\
    )
)

REM ---------- 5. Kiem tra GPU ----------
echo.
where nvidia-smi >nul 2>nul
if errorlevel 1 (
    echo [!] Khong thay card NVIDIA. App van chay duoc nhung bang CPU va rat cham.
) else (
    for /f "delims=" %%G in ('nvidia-smi --query-gpu^=name^,driver_version --format^=csv^,noheader') do echo [OK] GPU: %%G
    echo      Can driver tu 525 tro len de dung CUDA 12.
)

echo.
echo ==========================================
echo   Xong. Chay run.bat de mo giao dien.
echo ==========================================
echo.
pause
exit /b 0


REM ---------- Ham tai ffmpeg ----------
:get_ffmpeg
set "FF_URL=https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
set "FF_ZIP=%TEMP%\videoocr_ffmpeg.zip"
set "FF_DIR=%TEMP%\videoocr_ffmpeg"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '%FF_URL%' -OutFile '%FF_ZIP%'"
if errorlevel 1 exit /b 1

if exist "%FF_DIR%" rmdir /s /q "%FF_DIR%"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "Expand-Archive -Path '%FF_ZIP%' -DestinationPath '%FF_DIR%' -Force"
if errorlevel 1 exit /b 1

if not exist "bin" mkdir "bin"
for /d %%D in ("%FF_DIR%\ffmpeg-*") do (
    copy /y "%%D\bin\ffmpeg.exe"  "bin\" >nul
    copy /y "%%D\bin\ffprobe.exe" "bin\" >nul
)

del /q "%FF_ZIP%" >nul 2>nul
rmdir /s /q "%FF_DIR%" >nul 2>nul

if not exist "bin\ffmpeg.exe" exit /b 1
if not exist "bin\ffprobe.exe" exit /b 1
exit /b 0
