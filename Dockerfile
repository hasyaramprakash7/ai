FROM python:3.11-slim

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
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "visva_sovereign:app", "--host", "0.0.0.0", "--port", "8000"]