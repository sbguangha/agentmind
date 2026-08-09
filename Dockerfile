FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# install uv (fast, dependency-pinned by uv.lock)
RUN pip install --no-cache-dir uv

# copy the manifest + source; uv.lock pins every dependency
COPY pyproject.toml uv.lock README.md ./
COPY agentmind ./agentmind

# install production dependencies + the project (console script: agentmind)
RUN uv sync --frozen --no-dev

# runtime config via environment variables (see docker-compose.yml / docs/deployment.md)
ENV AGENTMIND_HOST=0.0.0.0 \
    AGENTMIND_PORT=8765 \
    AGENTMIND_DATA_DIR=/app/data \
    AGENTMIND_WORKSPACE=/app/workspace

# persistent state: sessions, memory, audio, config
VOLUME ["/app/data"]

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/', timeout=3)"

CMD ["uv", "run", "--no-sync", "agentmind"]
