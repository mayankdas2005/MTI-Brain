#!/bin/bash
PORT=8001
lsof -ti:$PORT | xargs kill -9 2>/dev/null || true
uvicorn app.main:app --host 0.0.0.0 --port $PORT --reload
