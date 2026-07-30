"""
FTS5 full-text search for the AgentNexus.

Provides:
  - ensure_fts_table(engine)           — idempotent FTS5 virtual table creation
  - rebuild_fts_index_if_empty(engine) — cold-start backfill from published documents
  - upsert_doc(db, ...)                — update the FTS index for one document
  - search(db, ...)                    — BM25-ranked full-text search

The doc_fts virtual table is NOT managed by Alembic; it is created at startup
via ensure_fts_table(). All operations use raw SQL — FTS5 virtual tables are
not compatible with SQLAlchemy ORM.

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 3.1, 3.4
"""

import sqlite3
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from agent_nexus.services.errors import AgentNexusError

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_CREATE_FTS_TABLE = """
CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts USING fts5(
    doc_id,
    project_space_id UNINDEXED,
    subproject_id    UNINDEXED,
    doc_type         UNINDEXED,
    content,
    tokenize = 'unicode61'
);
"""

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ensure_fts_table(engine) -> None:
    """
    Idempotently create the doc_fts FTS5 virtual table.

    Safe to call multiple times — uses CREATE VIRTUAL TABLE IF NOT EXISTS.
    Must be called after Base.metadata.create_all().

    Requirement 1.1
    """
    with engine.connect() as conn:
        conn.execute(text(_CREATE_FTS_TABLE))
        conn.commit()


def rebuild_fts_index_if_empty(engine) -> None:
    """
    If doc_fts is empty, backfill it from all currently published documents.

    For each document, only the latest published version is indexed.
    This handles cold starts where push_document was called before FTS existed.

    Requirement 1.5
    """
    with engine.connect() as conn:
        row = conn.execute(text("SELECT COUNT(*) FROM doc_fts")).fetchone()
        count = row[0] if row else 0
        if count > 0:
            return

        # Fetch latest published version content for each doc_id
        rows = conn.execute(text("""
            SELECT
                d.id            AS doc_id,
                d.project_space_id,
                d.subproject_id,
                d.doc_type,
                dvc.content
            FROM documents d
            JOIN document_versions dv
                ON dv.document_id = d.id
                AND dv.project_space_id = d.project_space_id
                AND dv.version = d.latest_version
                AND dv.status = 'published'
            JOIN blobs dvc
                ON dvc.project_space_id = dv.project_space_id
                AND dvc.content_hash = dv.content_hash
        """)).fetchall()

        for row in rows:
            conn.execute(
                text("""
                    INSERT INTO doc_fts(doc_id, project_space_id, subproject_id, doc_type, content)
                    VALUES (:doc_id, :project_space_id, :subproject_id, :doc_type, :content)
                """),
                {
                    "doc_id": row[0],
                    "project_space_id": row[1],
                    "subproject_id": row[2],
                    "doc_type": row[3],
                    "content": row[4],
                },
            )
        conn.commit()


def upsert_doc(
    db: Session,
    doc_id: str,
    project_space_id: str,
    subproject_id: str,
    doc_type: str,
    content: str,
) -> None:
    """
    Update the FTS index for a single document (latest published version).

    FTS5 does not support INSERT OR REPLACE, so we use DELETE + INSERT.
    Must be called within an active DB session after the document version
    has been flushed to the main tables.

    Requirements 1.2, 1.3, 1.4
    """
    # Delete existing entry for this doc_id (if any)
    db.execute(
        text("DELETE FROM doc_fts WHERE doc_id = :doc_id"),
        {"doc_id": doc_id},
    )
    # Insert new entry
    db.execute(
        text("""
            INSERT INTO doc_fts(doc_id, project_space_id, subproject_id, doc_type, content)
            VALUES (:doc_id, :project_space_id, :subproject_id, :doc_type, :content)
        """),
        {
            "doc_id": doc_id,
            "project_space_id": project_space_id,
            "subproject_id": subproject_id,
            "doc_type": doc_type,
            "content": content,
        },
    )


def search(
    db: Session,
    project_space_id: str,
    query: str,
    doc_type: Optional[str] = None,
    subproject_id: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """
    Full-text search across published documents in a project space.

    Supports FTS5 query syntax:
      - Keywords:      authentication
      - Phrases:       "user authentication"
      - Prefix:        auth*
      - Boolean:       authentication NOT oauth  (use NOT, not AND NOT)

    Results are ranked by BM25 relevance (rank ascending = most relevant first).
    Snippets use >>> / <<< to mark matched terms.

    Raises AgentNexusError(INVALID_QUERY) on FTS5 syntax errors.

    Requirements 2.2, 2.3, 2.4, 2.5, 2.6, 2.7
    """
    # Build WHERE clause dynamically based on optional filters.
    # project_space_id is always required (multi-tenant isolation).
    where_clauses = [
        "doc_fts MATCH :query",
        "project_space_id = :project_space_id",
    ]
    params: dict = {
        "query": query,
        "project_space_id": project_space_id,
        "limit": limit,
    }

    if doc_type is not None:
        where_clauses.append("doc_type = :doc_type")
        params["doc_type"] = doc_type

    if subproject_id is not None:
        where_clauses.append("subproject_id = :subproject_id")
        params["subproject_id"] = subproject_id

    where_sql = " AND ".join(where_clauses)

    # snippet() column index: doc_id=0, project_space_id=1, subproject_id=2,
    # doc_type=3, content=4  → index 4
    sql = f"""
        SELECT
            doc_id,
            project_space_id,
            subproject_id,
            doc_type,
            snippet(doc_fts, 4, '>>>', '<<<', '...', 64) AS snippet,
            rank
        FROM doc_fts
        WHERE {where_sql}
        ORDER BY rank
        LIMIT :limit
    """

    try:
        rows = db.execute(text(sql), params).fetchall()
    except Exception as exc:
        # sqlite3.OperationalError surfaces as a generic Exception through SQLAlchemy;
        # check the message to distinguish FTS syntax errors from other DB errors.
        msg = str(exc)
        if "fts5" in msg.lower() or "syntax error" in msg.lower() or "no such column" in msg.lower():
            raise AgentNexusError(
                error_code="INVALID_QUERY",
                message=f"Invalid FTS5 query syntax: {msg}",
                details={"query": query},
            )
        raise

    return [
        {
            "doc_id": row[0],
            "project_space_id": row[1],
            "subproject_id": row[2],
            "doc_type": row[3],
            "snippet": row[4],
            "rank": row[5],
        }
        for row in rows
    ]


def remove_doc(db: Session, doc_id: str) -> None:
    """
    Remove a document from the FTS index.

    Called when a document is soft-deleted so it no longer appears
    in search results. The version history in the main tables is untouched.
    """
    db.execute(
        text("DELETE FROM doc_fts WHERE doc_id = :doc_id"),
        {"doc_id": doc_id},
    )
