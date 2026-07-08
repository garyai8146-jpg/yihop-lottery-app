@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title 藝鍋物抽獎系統

set "LOG=%~dp0yihop_lottery_startup.log"
> "%LOG%" echo [%date% %time%] 藝鍋物抽獎系統啟動紀錄

call :main
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo ========================================
    echo [啟動失敗] 錯誤代碼：%RC%
    echo 完整紀錄：%LOG%
    echo ========================================
    echo.
    powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Test-Path -LiteralPath $env:LOG) { Get-Content -LiteralPath $env:LOG -Tail 80 }"
) else (
    echo 系統已停止。
)
echo.
echo 此視窗不會自動關閉，請按任意鍵結束。
pause >nul
exit /b %RC%

:main
echo ========================================
echo 藝鍋物 - 開鍋抽好禮
echo ========================================
echo.

echo [1/5] 檢查 Python...
set "PYTHON_EXE="
for /f "usebackq delims=" %%P in (`py -3 -c "import sys; print(sys.executable)" 2^>nul`) do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
if not defined PYTHON_EXE (
    for /f "usebackq delims=" %%P in (`python -c "import sys; print(sys.executable)" 2^>nul`) do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
)
if not defined PYTHON_EXE (
    >> "%LOG%" echo [ERROR] 找不到可用的 Python。
    echo [錯誤] 找不到 Python。
    echo 請先安裝 Python 3.11，安裝時勾選 Add Python to PATH。
    exit /b 10
)

"%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >> "%LOG%" 2>&1
if errorlevel 1 (
    >> "%LOG%" echo [ERROR] Python 版本低於 3.10。
    echo [錯誤] Python 版本過舊，請安裝 Python 3.11 或更新版本。
    exit /b 11
)
"%PYTHON_EXE%" -c "import sys; print('[Python]', sys.version); print('[Executable]', sys.executable)" >> "%LOG%" 2>&1

echo [2/5] 準備專用環境...
if exist ".venv" if not exist ".venv\Scripts\python.exe" (
    >> "%LOG%" echo [WARN] 發現不完整的 .venv，正在重建。
    rmdir /s /q ".venv" >> "%LOG%" 2>&1
)
if not exist ".venv\Scripts\python.exe" (
    "%PYTHON_EXE%" -m venv .venv >> "%LOG%" 2>&1
    if errorlevel 1 (
        echo [錯誤] 無法建立 Python 專用環境。
        exit /b 20
    )
)

echo [3/5] 安裝或更新必要套件...
".venv\Scripts\python.exe" -m pip install --upgrade pip >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [錯誤] pip 更新失敗，請確認網路連線。
    exit /b 30
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [錯誤] 套件安裝失敗，請確認網路連線與 Python 版本。
    exit /b 31
)

echo [4/5] 驗證程式...
".venv\Scripts\python.exe" -m py_compile app.py >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [錯誤] app.py 程式檢查失敗。
    exit /b 40
)
".venv\Scripts\python.exe" -c "import streamlit, pandas; print('[Streamlit]', streamlit.__version__); print('[Pandas]', pandas.__version__)" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [錯誤] Streamlit 或 Pandas 無法載入。
    exit /b 41
)

set "PORT=8501"
:port_check
netstat -ano -p tcp | findstr /R /C:":!PORT! .*LISTENING" >nul 2>nul
if not errorlevel 1 (
    set /a PORT+=1
    if !PORT! GTR 8510 (
        >> "%LOG%" echo [ERROR] 8501 到 8510 皆被占用。
        echo [錯誤] 找不到可用的網站連接埠。
        exit /b 50
    )
    goto :port_check
)

echo [5/5] 啟動抽獎網站...
echo.
echo 客人頁：http://localhost:!PORT!
echo 管理後台：http://localhost:!PORT!/?admin=1
echo 初始管理 PIN：1688
echo.
echo 系統運行期間請保留此視窗；關閉視窗即可停止網站。
echo 啟動紀錄：%LOG%
echo.
>> "%LOG%" echo [INFO] 使用連接埠 !PORT!。

start "" /b powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Sleep -Seconds 4; Start-Process ('http://localhost:' + $env:PORT)"
".venv\Scripts\python.exe" -m streamlit run app.py --server.address 0.0.0.0 --server.port !PORT! --server.headless true --browser.gatherUsageStats false >> "%LOG%" 2>&1
set "STREAMLIT_RC=!ERRORLEVEL!"
if not "!STREAMLIT_RC!"=="0" (
    echo [錯誤] Streamlit 已意外停止。
    exit /b !STREAMLIT_RC!
)
exit /b 0
