@echo off
REM Gemini Voice Generator - Startup Script
REM Starts the FastAPI server on http://127.0.0.1:8015

cd /d "%~dp0"

echo Starting Gemini Voice Generator...
echo.
echo To use the app:
echo 1. Open your browser to http://127.0.0.1:8015
echo 2. Paste your Gemini API key in the UI
echo 3. Load an example script or paste your own transcript
echo 4. Click "Generate WAV files"
echo.
echo Press Ctrl+C to stop the server.
echo.

REM Activate venv and start server
call .venv\Scripts\activate.bat
python -m uvicorn app.main:app --host 127.0.0.1 --port 8015 --reload

pause
