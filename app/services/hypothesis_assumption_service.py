"""Phase 3 Slice D: ``HypothesisAssumptionService``.

Mirrors ``CycleIntelligenceService``'s transactional shape exactly: each
write method is one atomic commit; revision-chain integrity (no
branching, no duplicate revision numbers, no second genesis, no two
concurrent writers producing competing "current" rows) is enforced at
the database level by ``InvestmentHypothesis``'s own constraints (see
``app/models/investment_hypothesis.py``'s module docstring) -- the
checks here are defense in depth, and the one race this service
translates into a clean domain error (two callers racing to extend the
same hypothesis chain) is handled exactly like
``CycleIntelligenceService.create_cycle_assessment``'s own race.

The citation-separation rule (Cycle Intelligence vs. company
Transformation) is upheld structurally: every citation/provenance check
below validates against ``CompanyExposure``/``ResearchProposition`` as
two entirely separate, never-conflated id spaces, and the one rule this
service enforces in code rather than in the schema is the P3-D
atomicity rule -- one ``ForecastAssumption`` row names exactly one
provenance source and reasons about exactly one driver.
"""

from __future__ import annotations

import sqlalchemy as sa

from app import db
from app.models.company import Company
from app.models.cycle_intelligence import CompanyExposure
from app.models.document import DocumentCompanyLink
from app.models.investment_hypothesis import (
    ForecastAssumption,
    InvestmentHypothesis,
    InvestmentHypothesisCitation,
)
from app.models.research_brain import Evidence, ExtractedFact
from app.models.research_coverage import CandidateFinding, ResearchProposition
from app.models.research_types import InvestmentOrigin
from app.utils.research_errors import ResearchNotFoundError, ResearchValidationError

_FORECAST_ASSUMPTION_PROVENANCE_FIELDS = (
    "cycle_exposure_id",
    "research_proposition_id",
    "fact_id",
    "candidate_finding_id",
    "evidence_id",
)

_CITATION_SOURCE_FIELDS = (
    "fact_id",
    "research_proposition_id",
    "company_exposure_id",
)


class HypothesisAssumptionService:
    """Create/query ``InvestmentHypothesis`` and ``ForecastAssumption``
    rows. No writes anywhere else in the schema."""

    @classmethod
    def _document_ids_for_company(cls, company_id: str) -> set[str]:
        return set(
            db.session.scalars(
                sa.select(DocumentCompanyLink.document_id).where(
                    DocumentCompanyLink.company_id == company_id
                )
            ).all()
        )

    @classmethod
    def _validate_citation_belongs_to_company(
        cls,
        *,
        company_id: str,
        fact_id: str | None,
        research_proposition_id: str | None,
        company_exposure_id: str | None,
        evidence_id: str | None = None,
        candidate_finding_id: str | None = None,
        field_name: str = "citation",
    ) -> None:
        if fact_id is not None:
            fact = db.session.get(ExtractedFact, fact_id)
            if fact is None:
                raise ResearchNotFoundError(
                    "extracted_fact_not_found", "ExtractedFact was not found"
                )
            if fact.company_id != company_id:
                raise ResearchValidationError(
                    {field_name: ["Fact must belong to the same company"]}
                )
        elif research_proposition_id is not None:
            proposition = db.session.get(ResearchProposition, research_proposition_id)
            if proposition is None:
                raise ResearchNotFoundError(
                    "research_proposition_not_found",
                    "ResearchProposition was not found",
                )
            if proposition.company_id != company_id:
                raise ResearchValidationError(
                    {
                        field_name: [
                            "ResearchProposition must belong to the same company"
                        ]
                    }
                )
        elif company_exposure_id is not None:
            exposure = db.session.get(CompanyExposure, company_exposure_id)
            if exposure is None:
                raise ResearchNotFoundError(
                    "company_exposure_not_found", "CompanyExposure was not found"
                )
            if exposure.company_id != company_id:
                raise ResearchValidationError(
                    {field_name: ["CompanyExposure must belong to the same company"]}
                )
        elif evidence_id is not None:
            evidence = db.session.get(Evidence, evidence_id)
            if evidence is None:
                raise ResearchNotFoundError(
                    "evidence_not_found", "Evidence was not found"
                )
            if evidence.document_id not in cls._document_ids_for_company(company_id):
                raise ResearchValidationError(
                    {field_name: ["Evidence must belong to the same company"]}
                )
        elif candidate_finding_id is not None:
            candidate = db.session.get(CandidateFinding, candidate_finding_id)
            if candidate is None:
                raise ResearchNotFoundError(
                    "candidate_finding_not_found", "CandidateFinding was not found"
                )
            if candidate.document_id not in cls._document_ids_for_company(company_id):
                raise ResearchValidationError(
                    {field_name: ["CandidateFinding must belong to the same company"]}
                )

    @classmethod
    def create_investment_hypothesis(
        cls,
        *,
        company_id: str,
        title: str,
        thesis_narrative: str,
        confidence: str,
        created_by_user_id: str,
        consensus_view: str | None = None,
        variant_view: str | None = None,
        why_market_is_wrong: str | None = None,
        falsifiers: str | None = None,
        origin: str = InvestmentOrigin.HUMAN_AUTHORED,
        citations: list[dict] | None = None,
        supersedes_revision_id: str | None = None,
        change_reason: str | None = None,
    ) -> InvestmentHypothesis:
        if db.session.get(Company, company_id) is None:
            raise ResearchNotFoundError("company_not_found", "Company was not found")

        citations = citations or []
        if not citations:
            raise ResearchValidationError(
                {"citations": ["At least one citation must be provided"]}
            )
        for citation in citations:
            sources = [citation.get(field) for field in _CITATION_SOURCE_FIELDS]
            if sum(1 for source in sources if source is not None) != 1:
                raise ResearchValidationError(
                    {
                        "citations": [
                            "Each citation must set exactly one of "
                            "fact_id/research_proposition_id/company_exposure_id"
                        ]
                    }
                )
            cls._validate_citation_belongs_to_company(
                company_id=company_id,
                fact_id=citation.get("fact_id"),
                research_proposition_id=citation.get("research_proposition_id"),
                company_exposure_id=citation.get("company_exposure_id"),
                field_name="citations",
            )

        if supersedes_revision_id is not None:
            predecessor = db.session.get(InvestmentHypothesis, supersedes_revision_id)
            if predecessor is None:
                raise ResearchNotFoundError(
                    "investment_hypothesis_not_found",
                    "InvestmentHypothesis to supersede was not found",
                )
            if predecessor.company_id != company_id:
                raise ResearchValidationError(
                    {
                        "supersedes_revision_id": [
                            "Must belong to the same company"
                        ]
                    }
                )
            if not change_reason or not change_reason.strip():
                raise ResearchValidationError(
                    {
                        "change_reason": [
                            "Required when superseding an existing hypothesis"
                        ]
                    }
                )
            revision_number = predecessor.revision_number + 1
        else:
            revision_number = 1

        hypothesis = InvestmentHypothesis(
            company_id=company_id,
            title=title,
            thesis_narrative=thesis_narrative,
            consensus_view=consensus_view,
            variant_view=variant_view,
            why_market_is_wrong=why_market_is_wrong,
            falsifiers=falsifiers,
            confidence=confidence,
            revision_number=revision_number,
            supersedes_revision_id=supersedes_revision_id,
            origin=origin,
            change_reason=change_reason,
            created_by_user_id=created_by_user_id,
        )
        try:
            db.session.add(hypothesis)
            db.session.flush()
            for citation in citations:
                db.session.add(
                    InvestmentHypothesisCitation(
                        investment_hypothesis_id=hypothesis.id,
                        fact_id=citation.get("fact_id"),
                        research_proposition_id=citation.get("research_proposition_id"),
                        company_exposure_id=citation.get("company_exposure_id"),
                    )
                )
            db.session.commit()
        except sa.exc.IntegrityError:
            db.session.rollback()
            raise ResearchValidationError(
                {
                    "supersedes_revision_id": [
                        "Already superseded by a concurrent call, or this "
                        "company already has a genesis hypothesis -- retry "
                        "against the current revision"
                    ]
                }
            )
        except Exception:
            db.session.rollback()
            raise
        return hypothesis

    @classmethod
    def record_forecast_assumption(
        cls,
        *,
        investment_hypothesis_id: str,
        scenario: str,
        assumption_statement: str,
        created_by_user_id: str,
        metric_slug: str | None = None,
        assumed_value: str | None = None,
        assumed_unit: str | None = None,
        cycle_exposure_id: str | None = None,
        research_proposition_id: str | None = None,
        fact_id: str | None = None,
        candidate_finding_id: str | None = None,
        evidence_id: str | None = None,
    ) -> ForecastAssumption:
        hypothesis = db.session.get(InvestmentHypothesis, investment_hypothesis_id)
        if hypothesis is None:
            raise ResearchNotFoundError(
                "investment_hypothesis_not_found",
                "InvestmentHypothesis was not found",
            )

        provenance = {
            "cycle_exposure_id": cycle_exposure_id,
            "research_proposition_id": research_proposition_id,
            "fact_id": fact_id,
            "candidate_finding_id": candidate_finding_id,
            "evidence_id": evidence_id,
        }
        if sum(1 for value in provenance.values() if value is not None) != 1:
            raise ResearchValidationError(
                {
                    "provenance": [
                        "Exactly one of cycle_exposure_id/research_proposition_id/"
                        "fact_id/candidate_finding_id/evidence_id must be set -- "
                        "the P3-D atomicity rule: one row, one driver, one source"
                    ]
                }
            )
        cls._validate_citation_belongs_to_company(
            company_id=hypothesis.company_id,
            fact_id=fact_id,
            research_proposition_id=research_proposition_id,
            company_exposure_id=cycle_exposure_id,
            evidence_id=evidence_id,
            candidate_finding_id=candidate_finding_id,
            field_name="provenance",
        )

        assumption = ForecastAssumption(
            investment_hypothesis_id=investment_hypothesis_id,
            scenario=scenario,
            assumption_statement=assumption_statement,
            metric_slug=metric_slug,
            assumed_value=assumed_value,
            assumed_unit=assumed_unit,
            cycle_exposure_id=cycle_exposure_id,
            research_proposition_id=research_proposition_id,
            fact_id=fact_id,
            candidate_finding_id=candidate_finding_id,
            evidence_id=evidence_id,
            created_by_user_id=created_by_user_id,
        )
        try:
            db.session.add(assumption)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return assumption

    @classmethod
    def get_current_investment_hypothesis(
        cls, company_id: str
    ) -> InvestmentHypothesis | None:
        """Deterministic by construction -- see module docstring. Uses
        ``scalar_one_or_none()`` so a constraint violation would fail
        loudly rather than silently picking a row."""

        superseded_ids = sa.select(InvestmentHypothesis.supersedes_revision_id).where(
            InvestmentHypothesis.supersedes_revision_id.is_not(None)
        )
        return db.session.scalars(
            sa.select(InvestmentHypothesis).where(
                InvestmentHypothesis.company_id == company_id,
                InvestmentHypothesis.id.not_in(superseded_ids),
            )
        ).one_or_none()

    @classmethod
    def get_investment_hypothesis_history(
        cls, company_id: str
    ) -> list[InvestmentHypothesis]:
        return list(
            db.session.scalars(
                sa.select(InvestmentHypothesis)
                .where(InvestmentHypothesis.company_id == company_id)
                .order_by(InvestmentHypothesis.revision_number.asc())
            ).all()
        )

    @classmethod
    def get_cycle_driver_assumptions(
        cls, investment_hypothesis_id: str, scenario: str
    ) -> list[ForecastAssumption]:
        """This forecast's cycle-driver assumptions, as a disjoint set
        from its transformation-driver assumptions -- queryable by
        provenance column, never by parsing ``assumption_statement``."""

        return list(
            db.session.scalars(
                sa.select(ForecastAssumption).where(
                    ForecastAssumption.investment_hypothesis_id == investment_hypothesis_id,
                    ForecastAssumption.scenario == scenario,
                    ForecastAssumption.cycle_exposure_id.is_not(None),
                )
            ).all()
        )

    @classmethod
    def get_transformation_driver_assumptions(
        cls, investment_hypothesis_id: str, scenario: str
    ) -> list[ForecastAssumption]:
        return list(
            db.session.scalars(
                sa.select(ForecastAssumption).where(
                    ForecastAssumption.investment_hypothesis_id == investment_hypothesis_id,
                    ForecastAssumption.scenario == scenario,
                    ForecastAssumption.research_proposition_id.is_not(None),
                )
            ).all()
        )
