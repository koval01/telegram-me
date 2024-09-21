#!/bin/bash

# Set the default port if not defined
PORT=${PORT:-8000}

# Check if an argument is passed for workers, otherwise calculate it
if [ -n "$1" ]; then
  WORKERS=$1
else
  WORKERS=$(( $(nproc) * 2 + 1 ))

  MAX_WORKERS=10
  if [ "$WORKERS" -gt "$MAX_WORKERS" ]; then
    WORKERS=$MAX_WORKERS
  fi
fi

# Run the uvicorn server with the specified settings
uvicorn app.main:app --workers "$WORKERS" --host=0.0.0.0 --port="$PORT" --loop uvloop --http h11
