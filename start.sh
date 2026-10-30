#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env. Fill GITHUB_TOKEN, GITHUB_WEBHOOK_SECRET and GROQ_API_KEY, then run again."
    exit 1
fi

if [ ! -x .venv/bin/python ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
    .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
fi

.venv/bin/python -c "from config import get_settings; get_settings()"

echo "Compliance Sentinel: http://127.0.0.1:8000/health"
echo "Swagger:             http://127.0.0.1:8000/docs"
exec .venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000
