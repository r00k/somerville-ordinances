#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

lsof -ti:8000 | xargs kill -9 2>/dev/null || true
sleep 1

echo "Starting server (model=${MODEL_NAME:-gpt-5.4})..."
nohup python3 main.py > /dev/null 2>&1 &
disown
sleep 2

curl -sf http://localhost:8000/health | python3 -m json.tool
echo ""
echo "Server running at http://localhost:8000 (pid $!)"
