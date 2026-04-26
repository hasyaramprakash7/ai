#!/bin/bash
set -e

echo "🛡️ Visva Citadel Pre‑flight Check"

# Wait for Ollama to be ready
until curl -s $OLLAMA_HOST/api/tags > /dev/null; do
  echo "Waiting for Ollama at $OLLAMA_HOST..."
  sleep 2
done

# Pull required models (blocking but ensures they exist)
for model in gemma4:e4b llama3.1:8b gemma2:2b qwen2:1.5b; do
  if ! ollama list | grep -q $model; then
    echo "Pulling $model..."
    ollama pull $model
  fi
done

exec "$@"