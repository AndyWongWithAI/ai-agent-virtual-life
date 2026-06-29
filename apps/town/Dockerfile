# apps/town/Dockerfile
# 基于 python:3.12-slim,装 uv,从 monorepo 根 uv sync 全部 workspace
# 启动 town FastAPI(uvicorn),--app-dir 让 uvicorn 知道 town 包路径
FROM python:3.12-slim

# curl 用于 docker healthcheck
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

# 先拷 lockfile 走更好的 Docker layer cache
COPY pyproject.toml uv.lock ./
COPY apps/ ./apps/
COPY packages/ ./packages/

# 全部 workspace 包 + 全部 groups(dev 也要,test 跑过用)
RUN uv sync --all-packages --all-groups

EXPOSE 8000

ENV PYTHONUNBUFFERED=1

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uv", "run", "uvicorn", "town.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app/apps/town/src"]
