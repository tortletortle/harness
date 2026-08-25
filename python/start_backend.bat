@echo off
cd /d D:\spot\harness\python
python -m uvicorn api.server:app --host 127.0.0.1 --port 8000
