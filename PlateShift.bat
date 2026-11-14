@echo off
rem no cd: relative file names must still point where the user meant
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
start "" pyw -3 -m plateshift %*
