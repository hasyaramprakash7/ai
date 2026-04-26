#!/bin/bash
set -e

echo "🔍 Preflight: checking core tools..."
command -v node >/dev/null 2>&1 || { echo "Node.js missing"; exit 1; }
command -v tsc >/dev/null 2>&1 || { echo "tsc missing (install typescript)"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "Python3 missing"; exit 1; }
echo "✅ Core tools present"

# Wait for Ollama to be ready
echo "⏳ Waiting for Ollama at ${OLLAMA_HOST:-http://ollama:11434}..."
until curl -s "${OLLAMA_HOST:-http://ollama:11434}/api/tags" > /dev/null 2>&1; do
    echo "   waiting..."
    sleep 2
done
echo "✅ Ollama is reachable"

# Pull models if they aren't already present
for model in gemma4:e4b llama3.1:8b gemma2:2b qwen2:1.5b; do
    if curl -s "${OLLAMA_HOST:-http://ollama:11434}/api/tags" | grep -q "\"name\":\"$model\""; then
        echo "✅ Model $model already present"
    else
        echo "⬇️  Pulling $model..."
        curl -s -X POST "${OLLAMA_HOST:-http://ollama:11434}/api/pull" -d "{\"name\":\"$model\"}"
        echo "✅ Pulled $model"
    fi
done

echo "🚀 Starting Visva Sovereign..."
exec uvicorn visva_sovereign:app --host 0.0.0.0 --port 8000