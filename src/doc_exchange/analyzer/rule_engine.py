"""
RuleEngineAnalyzer: rule-based impact analysis using doc_type × subproject_type mapping.

Covers Requirement 13.2.
"""

from doc_exchange.models.entities import Document, DocumentVersion, SubProject

from .base import AffectedProject, AnalysisResult, Analyzer, TaskTemplate

# Mapping: (doc_type, subproject_type) -> list of TaskTemplate
_RULES: dict[tuple[str, str], list[TaskTemplate]] = {
    ("requirement", "testing"): [
        TaskTemplate(
            title="Review requirement changes",
            description="Requirement document updated, please review and update test cases.",
        )
    ],
    ("requirement", "development"): [
        TaskTemplate(
            title="Review requirement changes",
            description="Requirement document updated, please review implementation.",
        )
    ],
    ("design", "testing"): [
        TaskTemplate(
            title="Review design changes",
            description="Design document updated, please review test strategy.",
        )
    ],
    ("design", "development"): [
        TaskTemplate(
            title="Review design changes",
            description="Design document updated, please review implementation plan.",
        )
    ],
    ("api", "testing"): [
        TaskTemplate(
            title="Review API changes",
            description="API document updated, please update integration tests.",
        )
    ],
    ("api", "development"): [
        TaskTemplate(
            title="Review API changes",
            description="API document updated, please review implementation.",
        )
    ],
    ("config", "ops"): [
        TaskTemplate(
            title="Review config changes",
            description="Config document updated, please review deployment configuration.",
        )
    ],
    # task doc_type: no automatic impact
}


class RuleEngineAnalyzer(Analyzer):
    """
    Rule-based analyzer: maps (doc_type, subproject_type) to affected projects and task templates.

    Requirement 13.2: Initial implementation based on doc_type × subproject_type mapping table.
    """

    RULES: dict[tuple[str, str], list[TaskTemplate]] = _RULES

    async def analyze(
        self,
        doc: Document,
        new_version: DocumentVersion,
        all_subprojects: list[SubProject],
    ) -> AnalysisResult:
        doc_type = doc.doc_type
        affected: list[AffectedProject] = []

        for subproject in all_subprojects:
            key = (doc_type, subproject.type)
            templates = self.RULES.get(key)
            if templates:
                affected.append(
                    AffectedProject(
                        project_id=subproject.id,
                        tasks=list(templates),
                    )
                )

        return AnalysisResult(
            affected_projects=affected,
            doc_id=doc.id,
            version=new_version.version,
        )
