@echo off
rem Open the GUI as a desktop window, for a desktop shortcut or a double-click.
rem Starts `labcli gui` if nothing answers on its port yet, waits for it, then
rem opens the page in an Edge app window (no tabs, no address bar).
setlocal
set "REPO=%~dp0.."
set "LABCLI=%REPO%\.venv\Scripts\labcli.exe"
set "URL=http://localhost:8080/"

if not exist "%LABCLI%" (
    echo No venv at "%REPO%\.venv".
    echo Create one and install the console:
    echo     py -m venv .venv
    echo     .venv\Scripts\pip install -e .
    pause
    exit /b 1
)

curl -s -o nul "%URL%" && goto open

rem The server gets its own minimized window: closing it stops the GUI, not a
rem run. If it cannot start (no login set, port taken) the window stays up to read.
cd /d "%REPO%"
start "labcli gui" /min cmd /c "chcp 65001 >nul & "%LABCLI%" gui || pause"

set /a TRIES=0
:wait
ping -n 2 127.0.0.1 >nul
curl -s -o nul "%URL%" && goto open
set /a TRIES+=1
if %TRIES% lss 15 goto wait
echo The GUI did not start; see the "labcli gui" window in the taskbar.
pause
exit /b 1

:open
rem Without Edge, the default browser opens it as an ordinary tab.
set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if exist "%EDGE%" (start "" "%EDGE%" --app=%URL%) else (start "" %URL%)
