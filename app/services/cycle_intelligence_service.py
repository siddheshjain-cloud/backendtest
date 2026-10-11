"""Phase 3 Slice C: ``CycleIntelligenceService``.

Mirrors ``ResearchCoverageService``'s transactional shape exactly: each
write method is one atomic commit, rolled back whole on any failure.
Revision-chain integrity (no branching supersession, no duplicate
revision numbers, no second genesis, no two concurrent writers producing
competing "current" rows) is enforced at the database level by the
constraints declared on ``CycleAssessment`` itself (see
``app/models/cycle_intelligence.py``'s module docstring) -- the checks
here are defense in depth, mirroring ``link_proposition_stage``'s own
belt-and-suspenders style, plus the one race this service must still
translate into a clean domain error: two callers racing to extend the
same chain collide on ``uq_cycle_assessment_supersedes`` at commit time,
which this service catches and reports exactly like
``promote_candidate_finding``'s existing concurrent-decision race.

Company-internal Transformation tracking is deliberately *not* handled
here -- it is ``ResearchCoverageService.create_proposition``/
``link_proposition_stage``/``get_proposition_timeline``, unchanged. The
citation-separation rule this slice exists to enforce is upheld simply by
this service never accepting a ``ResearchProposition``/``PropositionLink``
id anywhere, and ``ResearchCoverageService`` never accepting a
``CycleObservation``/``CycleAssessment``/``CompanyExposure`` id anywhere --
two independently queryable mechanisms, not one shared table.
"""

from __future__ import annotations

import logging
from datetime import date

import sqlalchemy as sa
import sqlalchemy.orm as so

from app import db
from app.models.alert_types import AlertEventType, AlertSeverity
from app.models.company import Company
from app.models.cycle_intelligence import (
    CompanyExposure,
    CycleAssessment,
    CycleAssessmentObservation,
    CycleObservation,
)
from app.models.document import Document
from app.models.research_brain import Evidence
from app.services.alert_service import AlertService
from app.utils.research_errors import ResearchNotFoundError, ResearchValidationError

logger = logging.getLogger(__name__)


class CycleIntelligenceService:
    """Create/query ``CycleObservation``, ``CycleAssessment`` and
    ``CompanyExposure`` rows. No writes anywhere else in the schema."""

    @classmethod
    def record_cycle_observation(
        cls,
        *,
        cycle_tag: str,
        observed_on: date,
        description: str,
        created_by_user_id: str,
        evidence_id: str | None = None,
        document_id: str | None = None,
        external_reference: str | None = None,
    ) -> CycleObservation:
        sources = [evidence_id, document_id, external_reference]
        if sum(1 for source in sources if source is not None) != 1:
            raise ResearchValidationError(
                {
                    "source": [
                        "Exactly one of evidence_id/document_id/"
                        "external_reference must be set"
                    ]
                }
            )

        if evidence_id is not None:
            if db.session.get(Evidence, evidence_id) is None:
                raise ResearchNotFoundError(
                    "evidence_not_found", "Evidence was not found"
                )
        elif document_id is not None:
            if db.session.get(Document, document_id) is None:
                raise ResearchNotFoundError(
                    "document_not_found", "Document was not found"
                )
        else:
            # "Identifiable" per the approved correction: a real citation,
            # not a placeholder -- not a new source-management framework,
            # just the same non-blank discipline already required of e.g.
            # CandidateFindingDecision.reason.
            if not external_reference or not external_reference.strip():
                raise ResearchValidationError(
                    {
                        "external_reference": [
                            "Must be non-blank and identify a real "
                            "external source"
                        ]
                    }
                )

        observation = CycleObservation(
            cycle_tag=cycle_tag,
            observed_on=observed_on,
            description=description,
            evidence_id=evidence_id,
            document_id=document_id,
            external_reference=(
                external_reference.strip() if external_reference else None
            ),
            created_by_user_id=created_by_user_id,
        )
        try:
            db.session.add(observation)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return observation

    @classmethod
    def create_cycle_assessment(
        cls,
        *,
        cycle_tag: str,
        scenario: str,
        narrative: str,
        observation_ids: list[str],
        created_by_user_id: str,
        confidence: str | None = None,
        supersedes_revision_id: str | None = None,
        change_reason: str | None = None,
    ) -> CycleAssessment:
        if not observation_ids:
            raise ResearchValidationError(
                {
                    "observation_ids": [
                        "At least one CycleObservation must be cited"
                    ]
                }
            )

        observations = {
            observation_id: db.session.get(CycleObservation, observation_id)
            for observation_id in observation_ids
        }
        missing = [oid for oid, obs in observations.items() if obs is None]
        if missing:
            raise ResearchNotFoundError(
                "cycle_observation_not_found",
                f"CycleObservation(s) not found: {', '.join(missing)}",
            )

        # Provenance integrity (approved correction): every cited
        # observation must actually belong to the cycle being assessed.
        mismatched = [
            oid for oid, obs in observations.items() if obs.cycle_tag != cycle_tag
        ]
        if mismatched:
            raise ResearchValidationError(
                {
                    "observation_ids": [
                        f"CycleObservation(s) {', '.join(mismatched)} do not "
                        f"belong to cycle_tag {cycle_tag!r}"
                    ]
                }
            )

        if supersedes_revision_id is not None:
            predecessor = db.session.get(CycleAssessment, supersedes_revision_id)
            if predecessor is None:
                raise ResearchNotFoundError(
                    "cycle_assessment_not_found",
                    "CycleAssessment to supersede was not found",
                )
            if predecessor.cycle_tag != cycle_tag or predecessor.scenario != scenario:
                raise ResearchValidationError(
                    {
                        "supersedes_revision_id": [
                            "Must match the same cycle_tag and scenario "
                            "as the new assessment"
                        ]
                    }
                )
            if not change_reason or not change_reason.strip():
                raise ResearchValidationError(
                    {
                        "change_reason": [
                            "Required when superseding an existing "
                            "assessment"
                        ]
                    }
                )
            revision_number = predecessor.revision_number + 1
        else:
            revision_number = 1

        assessment = CycleAssessment(
            cycle_tag=cycle_tag,
            scenario=scenario,
            revision_number=revision_number,
            supersedes_revision_id=supersedes_revision_id,
            narrative=narrative,
            confidence=confidence,
            change_reason=change_reason,
            created_by_user_id=created_by_user_id,
        )
        try:
            db.session.add(assessment)
            db.session.flush()
            for observation_id in observation_ids:
                db.session.add(
                    CycleAssessmentObservation(
                        cycle_assessment_id=assessment.id,
                        cycle_observation_id=observation_id,
                    )
                )
            db.session.commit()
        except sa.exc.IntegrityError:
            db.session.rollback()
            raise ResearchValidationError(
                {
                    "supersedes_revision_id": [
                        "Already superseded by a concurrent call, or this "
                        "(cycle_tag, scenario) already has a genesis "
                        "assessment -- retry against the current revision"
                    ]
                }
            )
        except Exception:
            db.session.rollback()
            raise

        if supersedes_revision_id is not None:
            try:
                AlertService.record_event(
                    event_type=AlertEventType.CYCLE_ASSESSMENT_SUPERSEDED,
                    source_table="cycle_assessment",
                    source_id=assessment.id,
                    severity=AlertSeverity.WATCH,
                    summary=(
                        f"Cycle view changed for {cycle_tag} ({scenario}): "
                        f"revision {assessment.revision_number}"
                    ),
                    details={
                        "cycle_tag": cycle_tag,
                        "scenario": scenario,
                        "supersedes_revision_id": supersedes_revision_id,
                    },
                    recipient_user_ids=AlertService.admin_recipient_ids(),
                )
            except Exception:
                logger.exception(
                    "AlertService failed to record cycle assessment alert "
                    "for %s (business write above is unaffected)", assessment.id,
                )
        return assessment

    @classmethod
    def record_company_exposure(
        cls,
        *,
        company_id: str,
        cycle_assessment_id: str,
        exposure_direction: str,
        rationale: str,
        created_by_user_id: str,
    ) -> CompanyExposure:
        if db.session.get(Company, company_id) is None:
            raise ResearchNotFoundError(
                "company_not_found", "Company was not found"
            )
        if db.session.get(CycleAssessment, cycle_assessment_id) is None:
            raise ResearchNotFoundError(
                "cycle_assessment_not_found", "CycleAssessment was not found"
            )

        exposure = CompanyExposure(
            company_id=company_id,
            cycle_assessment_id=cycle_assessment_id,
            exposure_direction=exposure_direction,
            rationale=rationale,
            created_by_user_id=created_by_user_id,
        )
        try:
            db.session.add(exposure)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return exposure

    @classmethod
    def get_current_cycle_assessment(
        cls, cycle_tag: str, scenario: str
    ) -> CycleAssessment | None:
        """The one row, if any, not referenced by any other row's
        ``supersedes_revision_id``. Deterministic by construction: the
        model's unique constraints make at most one such row possible per
        ``(cycle_tag, scenario)``, so this uses ``scalar_one_or_none()``
        -- which raises loudly on more than one match -- rather than an
        ``ORDER BY ... LIMIT 1`` that would silently mask a violation.
        """

        superseded_ids = sa.select(CycleAssessment.supersedes_revision_id).where(
            CycleAssessment.supersedes_revision_id.is_not(None)
        )
        return db.session.scalars(
            sa.select(CycleAssessment).where(
                CycleAssessment.cycle_tag == cycle_tag,
                CycleAssessment.scenario == scenario,
                CycleAssessment.id.not_in(superseded_ids),
            )
        ).one_or_none()

    @classmethod
    def get_cycle_assessment_history(
        cls, cycle_tag: str, scenario: str
    ) -> list[CycleAssessment]:
        """Every revision of one ``(cycle_tag, scenario)`` chain, oldest
        first, mirroring ``get_proposition_timeline``'s shape."""

        return list(
            db.session.scalars(
                sa.select(CycleAssessment)
                .where(
                    CycleAssessment.cycle_tag == cycle_tag,
                    CycleAssessment.scenario == scenario,
                )
                .order_by(CycleAssessment.revision_number.asc())
            ).all()
        )

    @classmethod
    def get_cycle_evidence_chain(cls, company_exposure_id: str) -> dict:
        """Walk ``CompanyExposure`` -> ``CycleAssessment`` ->
        ``CycleAssessmentObservation`` -> ``CycleObservation`` ->
        (Evidence | Document | external_reference), independently of any
        Transformation-side (``ResearchProposition``) query. Used by the
        citation-separation acceptance test to prove this chain is
        resolvable on its own, by type, with no narrative parsing.
        """

        exposure = db.session.get(CompanyExposure, company_exposure_id)
        if exposure is None:
            raise ResearchNotFoundError(
                "company_exposure_not_found", "CompanyExposure was not found"
            )

        assessment = db.session.get(CycleAssessment, exposure.cycle_assessment_id)
        links = db.session.scalars(
            sa.select(CycleAssessmentObservation)
            .where(CycleAssessmentObservation.cycle_assessment_id == assessment.id)
            .options(so.joinedload(CycleAssessmentObservation.cycle_observation))
        ).all()

        return {
            "company_exposure_id": exposure.id,
            "exposure_direction": exposure.exposure_direction,
            "cycle_assessment_id": assessment.id,
            "cycle_tag": assessment.cycle_tag,
            "scenario": assessment.scenario,
            "revision_number": assessment.revision_number,
            "observations": [
                {
                    "cycle_observation_id": link.cycle_observation.id,
                    "evidence_id": link.cycle_observation.evidence_id,
                    "document_id": link.cycle_observation.document_id,
                    "external_reference": link.cycle_observation.external_reference,
                }
                for link in links
            ],
        }
