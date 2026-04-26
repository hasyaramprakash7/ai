FROM python:3.11-slim

# Install system dependencies: Node.js (for JS validation) and TypeScript compiler
RUN apt-get update && \
    apt-get install -y curl gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    npm install -g typescript && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /visva_engine

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY visva_sovereign.py .
COPY scripts/preflight.sh /usr/local/bin/preflight.sh
RUN chmod +x /usr/local/bin/preflight.sh

# No Ollama pull here – models are pulled at runtime by preflight.sh

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/preflight.sh"]
CMD ["uvicorn", "visva_sovereign:app", "--host", "0.0.0.0", "--port", "8000"]