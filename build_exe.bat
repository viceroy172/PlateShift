@echo off
rem builds dist\PlateShift.exe (needs: py -m venv .venv && .venv\Scripts\pip install pyinstaller)
cd /d "%~dp0"
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name PlateShift ^
  --paths . --workpath build --distpath dist --specpath build packaging\plateshift_app.py
