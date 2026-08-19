@echo off
chcp 65001 >nul
title نقشه قطعی برق قائم‌شهر
cd /d "%~dp0"
echo ============================================
echo   نقشه لحظه‌ای قطعی برق قائم‌شهر
echo   آدرس: http://127.0.0.1:8765
echo   (این پنجره را باز نگه دارید — با بستنش سرور خاموش می‌شود)
echo ============================================
set "PY=python"
where python >nul 2>nul
if errorlevel 1 (
  where py >nul 2>nul && set "PY=py"
)
%PY% server.py 8765
pause
