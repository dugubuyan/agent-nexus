"""
AnalyzerService: wraps an Analyzer with fallback to RuleEngineAnalyzer on failure.

Covers Requirement 13.4.
"""

from doc_exchange.models.entities import Document, DocumentVersion, SubProject
from doc_exchange.services.audit_log_service import AuditLogService

from .base import AnalysisResult, Analyzer
from .rule_engine import RuleEngineAnalyzer


class AnalyzerService:
    """
    Wraps a primary Analyzer with automatic fallback to RuleEngineAnalyzer.

    Requirement 13.4: When the Analyzer fails, the system falls back to the
    default rule engine and logs the failure reason to AuditLog.
    """

    def __init__(
        self,
        analyzer: Analyzer,
        fallback: RuleEngineAnalyzer,
        audit_log_service: AuditLogService,
    ) -> None:
        self._analyzer = analyzer
        self._fallback = fallback
        self._audit_log_service = audit_log_service

    async def analyze(
        self,
        doc: Document,
        new_version: DocumentVersion,
        all_subprojects: list[SubProject],
    ) -> AnalysisResult:
        """
        Run the primary analyzer; on failure, log to AuditLog and fall back to rule engine.
        """
        try:
            return await self._analyzer.analyze(doc, new_version, all_subprojects)
        except Exception as exc:
            # Log the failure (Requirement 13.4)
            self._audit_log_service.log(
                operation_type="analyzer_failure",
                operator_project_id="system",
                target_id=doc.id,
                result="failure",
                project_space_id=doc.project_space_id,
                detail=f"Primary analyzer failed: {exc!r}. Falling back to RuleEngineAnalyzer.",
            )
            # Degrade to rule engine
            return await self._fallback.analyze(doc, new_version, all_subprojects)
