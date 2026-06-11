@echo off
cd /d "%~dp0"
echo ============================================
echo  Bulletin Scan — Scraper de Prospects
echo ============================================
echo.
echo [1/2] Buildando imagem Docker...
docker build -t bulletin-refiner scraper/
if %ERRORLEVEL% NEQ 0 (
    echo ERRO no build. Verifique se o Docker esta rodando.
    pause & exit /b 1
)
echo.
echo [2/2] Buscando dados reais + scraping dos sites...
docker run --rm -v "%CD%:/project" bulletin-refiner
echo.
echo Pronto! Abra app\mapa_prospects.html no browser.
pause
