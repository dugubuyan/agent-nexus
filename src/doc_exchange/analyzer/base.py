"""
Analyzer base classes and result models.

Covers Requirements 13.1, 13.2, 13.3, 13.4.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from doc_exchange.models.entities import Document, DocumentVersion, SubProject


@dataclass
class TaskTemplate:
    """A suggested task to be created for an affected sub-project."""
    title: str
    description: str


@dataclass
class AffectedProject:
    """A sub-project affected by a document change, with suggested tasks."""
    project_id: str
    tasks: list[TaskTemplate] = field(default_factory=list)


@dataclass
class AnalysisResult:
    """Result of an impact analysis for a document version change."""
    affected_projects: list[AffectedProject]
    doc_id: str
    version: int


class Analyzer(ABC):
    """
    Abstract base class for document impact analyzers.

    Requirement 13.1: The system calls impact analysis through this unified
    interface, without depending on a concrete implementation.
    """

    @abstractmethod
    async def analyze(
        self,
        doc: Document,
        new_version: DocumentVersion,
        all_subprojects: list[SubProject],
    ) -> AnalysisResult:
        """
        Analyze the impact of a new document version.

        Returns affected sub-projects and suggested task descriptions.
        """
        ...
