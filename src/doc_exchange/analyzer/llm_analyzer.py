"""
LLMAnalyzer: placeholder for future LLM-based impact analysis.

Covers Requirement 13.3.
"""

from doc_exchange.models.entities import Document, DocumentVersion, SubProject

from .base import AnalysisResult, Analyzer


class LLMAnalyzer(Analyzer):
    """
    Future implementation: calls an LLM to analyze document change impact.

    Requirement 13.3: The Analyzer implementation can be replaced with an LLM
    analyzer without modifying calling code.

    This placeholder raises NotImplementedError until the LLM integration is built.
    """

    async def analyze(
        self,
        doc: Document,
        new_version: DocumentVersion,
        all_subprojects: list[SubProject],
    ) -> AnalysisResult:
        raise NotImplementedError(
            "LLMAnalyzer is not yet implemented. "
            "Use RuleEngineAnalyzer or configure a concrete LLM integration."
        )
