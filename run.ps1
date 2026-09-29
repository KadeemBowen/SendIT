# Start the app on http://localhost:8000 (reachable from other devices on your network too).
& "$PSScriptRoot\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
