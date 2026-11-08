@echo off
chcp 65001 > nul
echo ======================================================
echo          Сборка SBSH Launcher в EXE файл
echo ======================================================
echo.

cd /d "%~dp0"

if exist "venv\Scripts\python.exe" (
    set "PYTHON_EXE=venv\Scripts\python.exe"
) else (
    set "PYTHON_EXE=python"
)

echo Используется Python: %PYTHON_EXE%
%PYTHON_EXE% build_exe.py

echo.
pause
