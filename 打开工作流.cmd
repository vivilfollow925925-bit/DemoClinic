@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m jupyterlab "Demo体验与自动修复工作流.ipynb"
) else (
    python -m jupyterlab "Demo体验与自动修复工作流.ipynb"
)
if errorlevel 1 pause
