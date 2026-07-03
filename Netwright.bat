@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul && (
    start "" pythonw scripts\run_dashboard.pyw
) || (
    start "" python scripts\run_dashboard.pyw
)
