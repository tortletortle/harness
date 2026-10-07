@echo off
setlocal
cd /d "%~dp0\.."

netstat -ano | findstr ":8000" | findstr "LISTENING" >nul
if errorlevel 1 (
  start "EAS API" python scripts\start_api.py
)

cd frontend
netstat -ano | findstr ":3000" | findstr "LISTENING" >nul
if errorlevel 1 (
  start "EAS Helm" cmd /c npm run dev
)
cd ..

set EDGE=
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe
if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" set EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe
if "%EDGE%"=="" (
  start http://localhost:3000
  goto :eof
)

set /a n=0
:wait
set /a n+=1
powershell -NoProfile -Command "try { $c=New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',3000); $c.Close(); exit 0 } catch { exit 1 }"
if errorlevel 1 (
  if %n% geq 30 goto :openanyway
  timeout /t 1 /nobreak >nul
  goto wait
)

:openanyway
start "" "%EDGE%" --new-window --app=http://localhost:3000
endlocal
