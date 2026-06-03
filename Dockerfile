FROM python:3.12-slim

WORKDIR /app

# Copy everything first
COPY pyproject.toml .
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .

# Install dependencies to system Python (not editable, ensures proper installation)
RUN pip install --no-cache-dir sqlalchemy alembic pydantic watchdog "mcp[cli]>=1.0.0" aiosqlite

# Also install the package itself
RUN pip install --no-cache-dir -e .

# Environment configuration
ENV DOC_EXCHANGE_HOST=0.0.0.0
ENV DOC_EXCHANGE_PORT=10086
ENV DOC_EXCHANGE_DB_URL=sqlite:///doc_exchange.db
ENV DOC_EXCHANGE_DOCS_ROOT=./workspace

EXPOSE 10086

# Initialize database and start server
CMD ["sh", "-c", "python -m alembic upgrade head && python src/main.py"]
