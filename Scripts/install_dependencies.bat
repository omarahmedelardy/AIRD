@echo off
setlocal
set PYTHONUTF8=1

echo [AIRD] Installing Python dependencies...
set "UE_PYTHON=C:\Program Files\Epic Games\UE_5.7\Engine\Binaries\ThirdParty\Python3\Win64\python.exe"
if exist "%UE_PYTHON%" (
  set "PY_BIN=%UE_PYTHON%"
) else (
  set "PY_BIN=python"
)

"%PY_BIN%" -m pip install --upgrade pip
"%PY_BIN%" -m pip install websockets pillow

echo [AIRD] Done.
endlocal
