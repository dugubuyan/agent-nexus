"""
Pydantic schemas for DocumentService request/response models.
"""

from datetime import datetime

from pydantic import BaseModel


class PushRequest(BaseModel):
    doc_id: str
    content: str
    pushed_by: str          # project_id or "system_llm"
    project_space_id: str
    metadata: dict = {}     # for config type, must contain "stage"


class PushResult(BaseModel):
    version: int
    doc_id: str
    status: str             # draft | published


class DocumentResult(BaseModel):
    doc_id: str
    content: str
    version: int
    pushed_at: datetime
    pushed_by: str
    status: str


class VersionMeta(BaseModel):
    version: int
    pushed_at: datetime
    pushed_by: str
    status: str
