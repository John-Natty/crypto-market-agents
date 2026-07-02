FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WHATSAPP_ENABLED=false \
    TRADING_ENABLED=false \
    WITHDRAWALS_ENABLED=false \
    ORDER_EXECUTION_ENABLED=false \
    EXCHANGE_MODE=disabled

WORKDIR /app

RUN python -m pip install --no-cache-dir --upgrade pip

COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts ./scripts
COPY tests ./tests
COPY reports/.gitkeep ./reports/.gitkeep

RUN python -m pip install --no-cache-dir -e .

RUN adduser --disabled-password --gecos "" appuser \
    && chown -R appuser:appuser /app

USER appuser

CMD ["crypto-market-agents", "--help"]
