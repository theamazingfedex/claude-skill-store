@echo off
rem Thin wrapper: run skill-store from a checkout without installing it.
rem Prefer "pip install -e ." which puts skill-store on PATH.
setlocal
set "PYTHONPATH=%~dp0..;%PYTHONPATH%"
where py >NUL 2>NUL
if %ERRORLEVEL% EQU 0 (
  py -3 -m skillstore %*
) else (
  python -m skillstore %*
)
exit /b %ERRORLEVEL%
