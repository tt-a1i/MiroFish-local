#!/bin/sh
set -eu

cd /app/backend

export SIMULATION_PYTHON="${SIMULATION_PYTHON:-/app/backend/.venv-simulation/bin/python}"

mkdir -p /app/backend/logs /app/backend/uploads

exec .venv/bin/python run.py
