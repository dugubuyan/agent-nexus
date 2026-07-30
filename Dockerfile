FROM python:3.12-slim

WORKDIR /app

# Source and packaging metadata
COPY pyproject.toml .
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .
# SDAOP reads onboarding templates + push tool from spec/ at the repo root.
# Without this, generate_instruction_file() breaks inside the container.
COPY spec/ spec/

# Editable install keeps the source tree in place so the web dashboard
# templates (src/agent_nexus/web/templates) and spec/ resolve at runtime.
RUN pip install --no-cache-dir -e .

# Defaults match src/main.py; override at `docker run` time as needed.
ENV AGENT_NEXUS_HOST=0.0.0.0
ENV AGENT_NEXUS_PORT=10086
ENV AGENT_NEXUS_DB_URL=sqlite:///agent_nexus.db
ENV AGENT_NEXUS_DOCS_ROOT=./workspace

EXPOSE 10086

# main() auto-creates the schema and FTS index on first run — no separate
# migration step needed for a fresh database.
CMD ["python", "-m", "agent_nexus.main"]
