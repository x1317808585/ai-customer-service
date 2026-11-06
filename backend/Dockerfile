# 后端和 MCP Server 共用这个镜像，启动命令在 docker-compose.yml 里
FROM python:3.12-slim-bookworm

# uv 版本和本机开发用的一致
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# 先只装依赖（严格按 uv.lock，不装测试用的 dev 组），只改代码时这一层可以复用缓存。
# torch 是 CPU 版；镜像里不带 BGE-M3 模型，容器里固定用关键词检索
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project

COPY . .
