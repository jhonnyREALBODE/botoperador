@echo off
cd /d "%~dp0"
echo Instalando o Playwright e o navegador dele (so precisa uma vez)...
python -m pip install --upgrade playwright
python -m playwright install chromium
echo.
echo PRONTO. Agora rode 1-LOGIN.bat
pause
