#!/bin/bash
set -e

echo "🛡️ Visva Citadel – Pre-flight"

OLLAMA_HOST="${OLLAMA_HOST:-http://ollama:11434}"

# Wait for Ollama API
until curl -s "$OLLAMA_HOST/api/tags" > /dev/null 2>&1; do
    echo "⏳ Waiting for Ollama..."
    sleep 2
done
echo "✅ Ollama reachable"

# Pull models via API
for model in "gemma4:e4b" "llama3.1:8b" "gemma2:2b" "qwen2:1.5b"; do
    if curl -s "$OLLAMA_HOST/api/tags" | grep -q "\"name\":\"$model\""; then
        echo "✅ $model present"
    else
        echo "⬇️  Pulling $model..."
        curl -s -X POST "$OLLAMA_HOST/api/pull" -d "{\"name\":\"$model\"}" -o /dev/null
        echo "✅ Pulled $model"
    fi
done

echo "🚀 Starting Visva Sovereign..."
exec "$@"