@echo off
rem Сборка LaptopCheck.exe (запускать в Windows 10/11 x64, Python 3.11+)
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem Ищем Python: сначала лаунчер "py", затем "python" (он может оказаться заглушкой Microsoft Store)
set "PY="
py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python -c "import sys" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo Python не найден. Установите Python 3.11+ с https://www.python.org/downloads/windows/
    goto :error
)
echo Используется: %PY%
%PY% --version

%PY% -m pip install -r requirements.txt || goto :error

%PY% -m PyInstaller --onefile --noconsole --uac-admin --clean --noconfirm ^
    --name LaptopCheck ^
    --exclude-module matplotlib --exclude-module PIL --exclude-module scipy --exclude-module pandas ^
    main.py || goto :error

rem Разрешаем извлекать флешку во время работы программы (тест USB-портов)
%PY% tools\set_swaprun.py dist\LaptopCheck.exe || goto :error

echo.
echo Готово: dist\LaptopCheck.exe - скопируйте файл на флешку.
exit /b 0

:error
echo.
echo СБОРКА НЕ УДАЛАСЬ
exit /b 1
