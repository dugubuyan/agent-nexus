"""
SubscriptionService: subscription rule management and initial subscription inference.

Covers Requirements 4.1 – 4.5.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from doc_exchange.models.entities import SubProject, Subscription
from doc_exchange.services.errors import DocExchangeError

# Initial subscription mapping by sub-project type (Requirement 4.4)
INITIAL_SUBSCRIPTION_MAP: dict[str, list[str]] = {
    "testing": ["api", "requirement"],
    "development": ["requirement", "design"],
    "ops": ["config", "design"],
}


class SubscriptionService:
    def __init__(self, db: Session):
        self._db = db

    # ------------------------------------------------------------------
    # Requirement 4.1, 4.2, 4.5: add a subscription rule
    # ------------------------------------------------------------------

    def add_rule(
        self,
        subscriber_project_id: str,
        project_space_id: str,
        target_doc_id: str | None = None,
        target_doc_type: str | None = None,
    ) -> Subscription:
        """
        Add a subscription rule for a sub-project.

        Raises DocExchangeError(PROJECT_NOT_FOUND) if subscriber_project_id does
        not exist as a SubProject in the given space (Requirement 4.5).
        At least one of target_doc_id or target_doc_type must be provided.
        """
        if not target_doc_id and not target_doc_type:
            raise DocExchangeError(
                error_code="MISSING_REQUIRED_FIELD",
                message="At least one of target_doc_id or target_doc_type must be provided.",
                details={"missing_fields": ["target_doc_id", "target_doc_type"]},
            )

        # Validate subscriber exists in this space (Requirement 4.5)
        subproject = (
            self._db.query(SubProject)
            .filter(
                SubProject.id == subscriber_project_id,
                SubProject.project_space_id == project_space_id,
            )
            .first()
        )
        if subproject is None:
            raise DocExchangeError(
                error_code="PROJECT_NOT_FOUND",
                message=f"Sub-project '{subscriber_project_id}' not found.",
                details={"project_id": subscriber_project_id},
            )

        rule = Subscription(
            id=str(uuid.uuid4()),
            project_space_id=project_space_id,
            subscriber_project_id=subscriber_project_id,
            target_doc_id=target_doc_id,
            target_doc_type=target_doc_type,
            created_at=datetime.now(timezone.utc),
        )
        self._db.add(rule)
        self._db.flush()
        return rule

    # ------------------------------------------------------------------
    # Requirement 4.3: remove a subscription rule
    # ------------------------------------------------------------------

    def remove_rule(self, rule_id: str, project_space_id: str) -> None:
        """
        Remove a subscription rule by id.

        No error is raised if the rule does not exist (Requirement 4.3).
        """
        rule = (
            self._db.query(Subscription)
            .filter(
                Subscription.id == rule_id,
                Subscription.project_space_id == project_space_id,
            )
            .first()
        )
        if rule is not None:
            self._db.delete(rule)
            self._db.flush()

    # ------------------------------------------------------------------
    # Requirement 4.1: get subscribers for a doc_id or doc_type
    # ------------------------------------------------------------------

    def get_subscribers(
        self,
        project_space_id: str,
        doc_id: str | None = None,
        doc_type: str | None = None,
    ) -> list[str]:
        """
        Return subscriber_project_ids that match either the exact doc_id OR the doc_type.

        Returns the union of both match sets (deduplicated).
        """
        from sqlalchemy import or_

        conditions = []
        if doc_id:
            conditions.append(Subscription.target_doc_id == doc_id)
        if doc_type:
            conditions.append(Subscription.target_doc_type == doc_type)

        if not conditions:
            return []

        rows = (
            self._db.query(Subscription.subscriber_project_id)
            .filter(
                Subscription.project_space_id == project_space_id,
                or_(*conditions),
            )
            .distinct()
            .all()
        )
        return [r[0] for r in rows]

    # ------------------------------------------------------------------
    # Requirement 4.1: list all rules for a subscriber
    # ------------------------------------------------------------------

    def list_rules(
        self, subscriber_project_id: str, project_space_id: str
    ) -> list[Subscription]:
        """Return all subscription rules for a given subscriber in the space."""
        return (
            self._db.query(Subscription)
            .filter(
                Subscription.subscriber_project_id == subscriber_project_id,
                Subscription.project_space_id == project_space_id,
            )
            .all()
        )

    # ------------------------------------------------------------------
    # Requirement 4.4: infer initial subscriptions by sub-project type
    # ------------------------------------------------------------------

    def infer_initial_subscriptions(
        self,
        subproject_id: str,
        subproject_type: str,
        project_space_id: str,
    ) -> list[Subscription]:
        """
        Create and persist initial subscription rules based on sub-project type mapping.

        Mapping:
          testing     → api, requirement
          development → requirement, design
          ops         → config, design

        Returns the list of created Subscription records.
        """
        doc_types = INITIAL_SUBSCRIPTION_MAP.get(subproject_type, [])
        rules: list[Subscription] = []
        for doc_type in doc_types:
            rule = self.add_rule(
                subscriber_project_id=subproject_id,
                project_space_id=project_space_id,
                target_doc_type=doc_type,
            )
            rules.append(rule)
        return rules
