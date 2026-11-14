@echo off
setlocal
rem Runs PlateShift from its source code, which needs Python 3.11 or newer with Tk.
rem Most people want PlateShift.exe from the Releases page instead: it needs nothing installed.
rem No cd: relative file names must still point where the user meant.
rem No ( ) blocks around %*: a file name like "Model (2).3mf" would break them.
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
set "CHECK=import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 11) else 1)"

rem 1. the python.org launcher
where py >nul 2>nul || goto try_python
py -3 -c "%CHECK%" >nul 2>nul || goto try_python
where pyw >nul 2>nul || goto py_console
start "" pyw -3 -m plateshift %*
exit /b 0
:py_console
start "" py -3 -m plateshift %*
exit /b 0

rem 2. a python on PATH. windows' "python" placeholder that opens the store fails the check
:try_python
where python >nul 2>nul || goto none
python -c "%CHECK%" >nul 2>nul || goto none
where pythonw >nul 2>nul || goto python_console
start "" pythonw -m plateshift %*
exit /b 0
:python_console
start "" python -m plateshift %*
exit /b 0

:none
echo.
echo  PlateShift couldn't find Python 3.11 or newer (with Tk) on this PC.
echo.
echo  Easiest: download PlateShift.exe instead. It needs nothing installed:
echo    https://github.com/viceroy172/PlateShift/releases/latest
echo.
echo  Or install Python from https://www.python.org/downloads/windows/
echo  keeping "tcl/tk and IDLE" and "py launcher" ticked, then run this again.
echo.
pause
exit /b 1
