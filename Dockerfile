FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]" 2>/dev/null || pip install --no-cache-dir -e .

# Copy source
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .

# Initialize database and start server
RUN python -m alembic upgrade head

EXPOSE 10086

ENV DOC_EXCHANGE_HOST=0.0.0.0
ENV DOC_EXCHANGE_PORT=10086
ENV DOC_EXCHANGE_DB_URL=sqlite:///doc_exchange.db
ENV DOC_EXCHANGE_DOCS_ROOT=./workspace

CMD ["python", "src/main.py"]
