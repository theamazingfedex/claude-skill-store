@echo off
rem Thin wrapper: run skill-store from a checkout without installing it.
rem Prefer "pip install -e ." which puts skill-store on PATH.
rem Uses the python on PATH (e.g. an active venv) and falls back to the py -3 launcher.
setlocal
set "PYTHONPATH=%~dp0..;%PYTHONPATH%"
where python >NUL 2>NUL
if %ERRORLEVEL% EQU 0 (
  python -m skillstore %*
) else (
  py -3 -m skillstore %*
)
exit /b %ERRORLEVEL%
