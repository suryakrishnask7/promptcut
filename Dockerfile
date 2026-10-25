FROM python:3.11-slim

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PORT=8000 \
    HOST=0.0.0.0

# Install system dependencies: FFmpeg, curl (for healthchecks), fonts for ASS captions
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency specifications first for optimal layer caching
COPY pyproject.toml README.md ./

# Install python dependencies and dev packages (for pytest inside container)
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir hatchling pytest pytest-cov pyyaml boto3 httpx uvicorn

# Copy project source, tests, and configuration
COPY src/ ./src/
COPY tests/ ./tests/
COPY scripts/ ./scripts/
COPY openapi.yaml .env.example ./

# Install promptcut in editable mode now that src/ exists
RUN pip install --no-cache-dir -e .

# Expose default port (Render sets $PORT dynamically)
EXPOSE 8000

# Default command: start PromptCut web service on 0.0.0.0 using dynamic $PORT from Render
CMD ["sh", "-c", "promptcut serve --host 0.0.0.0 --port ${PORT:-8000}"]
