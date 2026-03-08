FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.6 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY watcher.py revert.py tag_fix.py ./
COPY checkers/ checkers/
COPY notifiers/ notifiers/

CMD ["/app/.venv/bin/python", "watcher.py"]
