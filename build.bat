@echo off
set VENV=%~dp0..\311venv\Scripts
"%VENV%\pyinstaller.exe" timstof_spectrum_viewer.spec -y > build_log.txt 2>&1
if %ERRORLEVEL% neq 0 (
    echo Build FAILED. See build_log.txt
    pause
    exit /b 1
)
echo Build SUCCESS: dist\timsTOF_Viewer\timsTOF_Viewer.exe
pause
