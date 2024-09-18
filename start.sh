#!/bin/bash

# Set the default port if not defined
PORT=${PORT:-8000}

# Calculate the number of workers
WORKERS=$(( $(nproc) * 2 + 1 ))

# Run the uvicorn server with the specified settings
uvicorn app.main:app --workers $WORKERS --host=0.0.0.0 --port="$PORT" --loop uvloop --http h11
