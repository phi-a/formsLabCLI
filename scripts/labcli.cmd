@echo off
rem Launch the lab console from a double-click or a bare `labcli` on PATH.
rem The venv lives in the checkout, so this resolves relative to the script and
rem needs no machine-specific paths.
setlocal
set "REPO=%~dp0.."
set "LABCLI=%REPO%\.venv\Scripts\labcli.exe"

if not exist "%LABCLI%" (
    echo No venv at "%REPO%\.venv".
    echo Create one and install the console:
    echo     py -m venv .venv
    echo     .venv\Scripts\pip install -e .
    pause
    exit /b 1
)

rem The console draws box-drawing characters; the default OEM codepage mangles them.
chcp 65001 >nul
title labcli

rem Run from the repo root so the default output directory is <repo>\outputs.
cd /d "%REPO%"
"%LABCLI%" %*
set "RC=%ERRORLEVEL%"

rem A clean `exit` closes the window; a crash stays up long enough to read.
if not "%RC%"=="0" pause
exit /b %RC%
