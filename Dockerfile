FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \\
    PYTHONDONTWRITEBYTECODE=1 \\
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies before source to keep the dependency layer cacheable.
COPY requirements.txt ./
RUN python -m pip install --upgrade pip \\
    && pip install -r requirements.txt

COPY bot ./bot
COPY railway.json ./railway.json

RUN mkdir -p /app/data \\
    && groupadd --system bot \\
    && useradd --system --gid bot --home-dir /app bot \\
    && chown -R bot:bot /app

USER bot

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD python -c "import os; os.kill(1, 0)"

CMD ["python", "-m", "bot.main"]
