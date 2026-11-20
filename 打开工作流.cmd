@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m jupyterlab "DemoClinic主工作流.ipynb"
) else (
    python -m jupyterlab "DemoClinic主工作流.ipynb"
)
if errorlevel 1 pause
