@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

echo.
echo   ============================================
echo     DriftBot - carros pra drift ate R$ 40 mil
echo   ============================================
echo.
echo    1  Ver o que tem agora (nao envia nada)
echo    2  Varrer e mandar no Telegram (uma vez)
echo    3  Deixar rodando (varre a cada 30 min)
echo    4  Testar se as fontes ainda funcionam
echo    5  Esquecer tudo e mandar de novo
echo.

set /p op="   Opcao: "

if "%op%"=="1" python driftbot.py --dry-run
if "%op%"=="2" python driftbot.py --once
if "%op%"=="3" python driftbot.py
if "%op%"=="4" python driftbot.py --test
if "%op%"=="5" python driftbot.py --once --reset

echo.
pause
