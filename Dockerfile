FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev
COPY alembic.ini ./
COPY db ./db
COPY knowledge ./knowledge
ENV PYTHONUNBUFFERED=1
CMD ["uv", "run", "--frozen", "--no-dev", "ai-stylist", "bot"]
