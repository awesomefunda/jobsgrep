FROM python:3.12-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[redis]"

# Copy application code
COPY jobsgrep/ jobsgrep/
COPY frontend/ frontend/
COPY data/ data/
COPY scripts/ scripts/
COPY DISCLAIMER.md .

# Create data dir
RUN mkdir -p /root/.jobsgrep/reports

EXPOSE 8080

COPY docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
