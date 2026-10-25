@echo off
setlocal
set TF_CPP_MIN_LOG_LEVEL=2
set "PYTHONPATH=%~dp0"
"%~dp0.venv\Scripts\python.exe" -m kalshi_predictor.dashboard %*
