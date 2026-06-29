# apps/town/Dockerfile
# 优化策略(2026-06-29 #67 任务):
# 1. uv 不 pip install,直接 COPY ghcr.io/astral-sh/uv 二进制(避免 26MB wheel 重下载)
# 2. layer 顺序:lockfile + workspace 成员先 COPY → uv sync 创建 venv → town source 最后 COPY
#    (town source 高频改动不会触发 venv 重 build)
# 3. uv cache 持久化(--mount=type=cache,BuildKit)
# 4. apt 一次到位装 curl + ca-certificates(健康检查 + uv 二进制 HTTPS)
# 5. 需要 BuildKit 启用(本地: DOCKER_BUILDKIT=1 docker build ...;GH Actions: docker/build-push-action@v5 默认开)

FROM python:3.12-slim AS base

# 一次到位装 curl(健康检查)+ ca-certificates(uv 二进制 HTTPS 下发)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 拷 uv 二进制,跳过 pip install uv(节省 ~11min cold build)
COPY --from=ghcr.io/astral-sh/uv:0.11.25 /uv /uvx /bin/

WORKDIR /app

# Lockfile + workspace 成员 + town pyproject.toml 先拷,uv sync 建 venv(慢,layer cache 友好)
COPY pyproject.toml uv.lock ./
COPY packages/ ./packages/
COPY apps/town/pyproject.toml ./apps/town/pyproject.toml

# uv cache 持久化(跨 build 复用,BuildKit 必需)
# town source 后拷,town source 高频改动不触发 venv layer 重建
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --all-packages --all-groups

# town source 最后拷(高频改动不触发 venv layer 重建)
COPY apps/ ./apps/

EXPOSE 8000
ENV PYTHONUNBUFFERED=1

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uv", "run", "uvicorn", "town.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app/apps/town/src"]