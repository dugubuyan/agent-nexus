"""
Pydantic schemas for DocumentService request/response models.
"""

from datetime import datetime

from pydantic import BaseModel


class PushRequest(BaseModel):
    doc_id: str
    content: str
    actor: str              # subproject_id for in-boundary writes, or "agent:<role>" for cross-boundary actors (Planner, etc). See v4-ideas §18.
    project_space_id: str
    metadata: dict = {}     # for config type, must contain "stage"
    base_version: int | None = None  # expected server version; None skips check


class PushResult(BaseModel):
    version: int
    doc_id: str
    status: str             # draft | published


class DocumentResult(BaseModel):
    doc_id: str
    content: str
    version: int
    pushed_at: datetime
    actor: str
    status: str


class VersionMeta(BaseModel):
    version: int
    pushed_at: datetime
    actor: str
    status: str
