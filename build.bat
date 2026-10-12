@echo off
REM Construit Cartoucheur.exe (Windows, Python 3.12 recommandé)
cd /d "%~dp0"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name Cartoucheur ^
  --hidden-import mido.backends.rtmidi cartoucheur.py
echo.
echo Termine : l'exe se trouve dans dist\Cartoucheur.exe
pause
