#!/usr/bin/env bash
# Sets up on first run, then launches the app. Usage, from this folder: bash run.sh
set -e
cd "$(dirname "$0")"
if [ ! -d venv ]; then
  echo "First run: creating a virtual environment and installing requirements..."
  python3 -m venv venv
  ./venv/bin/pip install -q -r requirements.txt
fi
./venv/bin/python -m streamlit run Dashboard.py
