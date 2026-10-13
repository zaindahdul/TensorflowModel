@echo off
set TF_CPP_MIN_LOG_LEVEL=2
"%~dp0.venv\Scripts\python.exe" "%~dp0dashboard.py" %*
