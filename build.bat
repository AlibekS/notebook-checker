@echo off
rem Сборка LaptopCheck.exe (запускать в Windows 10/11 x64, Python 3.11+)
setlocal
cd /d "%~dp0"

python -m pip install -r requirements.txt || goto :error

python -m PyInstaller --onefile --noconsole --uac-admin --clean --noconfirm ^
    --name LaptopCheck ^
    --exclude-module matplotlib --exclude-module PIL --exclude-module scipy --exclude-module pandas ^
    main.py || goto :error

rem Разрешаем извлекать флешку во время работы программы (тест USB-портов)
python tools\set_swaprun.py dist\LaptopCheck.exe || goto :error

echo.
echo Готово: dist\LaptopCheck.exe - скопируйте файл на флешку.
exit /b 0

:error
echo.
echo СБОРКА НЕ УДАЛАСЬ
exit /b 1
