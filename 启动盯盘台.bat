@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" (
  where py >nul 2>&1
  if errorlevel 1 (
    python -m venv .venv
  ) else (
    py -3 -m venv .venv
  )
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -c "import fastapi,httpx,uvicorn,dotenv,websockets,tzdata,socksio; from pydantic import model_validator; from importlib.metadata import version; assert tuple(map(int,version('starlette').split('.')[:2])) >= (1,3) and tuple(map(int,version('h11').split('.')[:2])) >= (0,16)" >nul 2>&1
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -r backend\requirements.txt
  if errorlevel 1 goto failed
)
if not exist "frontend\dist\index.html" (
  where npm >nul 2>&1
  if errorlevel 1 goto failed
  pushd frontend
  call npm ci --no-audit --no-fund
  if errorlevel 1 goto frontend_failed
  call npm run build
  if errorlevel 1 goto frontend_failed
  popd
)
if not exist ".env" copy ".env.example" ".env" >nul
".venv\Scripts\python.exe" -m backend.launch
if errorlevel 1 goto failed
exit /b 0

:frontend_failed
popd
:failed
echo Pulse could not start. Check Python 3.11+, network access and the error above.
pause
exit /b 1
