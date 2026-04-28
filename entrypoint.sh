#!/bin/bash
set -e

OLLAMA_HOST="${OLLAMA_HOST:-http://ollama:11434}"

echo "⏳ Waiting for Ollama at $OLLAMA_HOST..."
until curl -s "$OLLAMA_HOST/api/tags" > /dev/null 2>&1; do
    echo "   waiting..."
    sleep 2
done
echo "✅ Ollama reachable"

# Pull required models if missing
for model in "gemma4:e4b" "llama3.1:8b" "gemma2:2b" "qwen2:1.5b"; do
    if curl -s "$OLLAMA_HOST/api/tags" | grep -q "\"name\":\"$model\""; then
        echo "✅ Model $model present"
    else
        echo "⬇️  Pulling $model..."
        curl -s -X POST "$OLLAMA_HOST/api/pull" -d "{\"name\":\"$model\"}" -o /dev/null
        echo "✅ Pulled $model"
    fi
done

echo "🚀 Starting Visva Sovereign..."
exec "$@"