"""Phase 3 Slice B: deterministic, rule-based financial narration.

Stateless and read-only -- nothing in this module writes to the
database, and there is no persisted narrative object. Every sentence is
rendered on demand from already-stored, already-cited Facts, and every
sentence carries the exact fact_id/evidence_id it was built from.

No AI/LLM dependency anywhere in this module (verified by the import-
boundary test in tests/test_milestone1_architecture_boundaries.py's
style -- see tests/research/test_financial_narration_service.py).

Two hard rules enforced by construction, not convention:
1. Descriptive clauses (what changed, by how much) render from any
   RAW/NORMALIZED/ADJUSTED Fact.
2. Causal/explanatory clauses (why it changed) render *only* from an
   ADJUSTED Fact's own stored justification -- the only place in this
   codebase a causal claim is ever evidence-gated by construction. No
   other heuristic or pattern-match ever produces a causal clause.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlalchemy as sa

from app import db
from app.models.financial_metric_definition import FinancialMetricDefinition
from app.models.research_brain import ExtractedFact
from app.models.research_coverage import FactDerivation, FactDerivationInput


@dataclass(frozen=True)
class NarrativeSentence:
    text: str
    fact_ids: tuple[str, ...] = field(default_factory=tuple)
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)


class FinancialNarrationService:
    """Renders descriptive/adjustment commentary. No writes, no AI call."""

    @classmethod
    def _current_facts_by_prefix(
        cls, company_id: str, period: str, prefix: str
    ) -> list[ExtractedFact]:
        superseded_ids = sa.select(ExtractedFact.supersedes_fact_id).where(
            ExtractedFact.supersedes_fact_id.is_not(None)
        )
        return list(
            db.session.scalars(
                sa.select(ExtractedFact).where(
                    ExtractedFact.company_id == company_id,
                    ExtractedFact.period == period,
                    ExtractedFact.fact_type.like(f"{prefix}%"),
                    ExtractedFact.id.not_in(superseded_ids),
                )
            ).all()
        )

    @classmethod
    def _definition_for(cls, fact_type: str, prefix: str) -> FinancialMetricDefinition | None:
        slug = fact_type[len(prefix):]
        return db.session.scalar(
            sa.select(FinancialMetricDefinition).where(
                FinancialMetricDefinition.slug == slug
            )
        )

    @classmethod
    def _render_normalized(cls, fact: ExtractedFact) -> NarrativeSentence:
        definition = cls._definition_for(fact.fact_type, "fin.norm.")
        label = definition.label if definition is not None else fact.fact_type
        unit = definition.standard_unit if definition is not None else ""
        unit_suffix = "%" if unit == "PERCENT" else (" days" if unit == "DAYS" else "")
        evidence_ids = tuple(link.evidence_id for link in fact.fact_evidence_links)
        return NarrativeSentence(
            text=f"{label} was {fact.value}{unit_suffix} in {fact.period}.",
            fact_ids=(fact.id,),
            evidence_ids=evidence_ids,
        )

    @classmethod
    def _render_adjusted(cls, fact: ExtractedFact) -> NarrativeSentence | None:
        """The only source of causal/explanatory text in this service --
        rendered strictly from the ADJUSTED Fact's own stored
        FactDerivation inputs, never guessed.
        """

        derivation = db.session.scalar(
            sa.select(FactDerivation).where(
                FactDerivation.derived_fact_id == fact.id
            )
        )
        if derivation is None:
            return None

        base_input = next(
            (
                i
                for i in derivation.inputs
                if i.role_label and i.role_label.startswith("Reported ")
            ),
            None,
        )
        justification_inputs = [
            i
            for i in derivation.inputs
            if i.role_label and i.role_label.startswith("Adjustment justification: ")
        ]
        if base_input is None or not justification_inputs:
            return None

        base_fact = db.session.get(ExtractedFact, base_input.input_fact_id)
        descriptions = ", ".join(
            i.role_label[len("Adjustment justification: "):]
            for i in justification_inputs
        )
        definition = cls._definition_for(fact.fact_type, "fin.adj.")
        label = definition.label if definition is not None else fact.fact_type

        fact_ids = [fact.id]
        evidence_ids = []
        if base_fact is not None:
            fact_ids.append(base_fact.id)
            evidence_ids.extend(
                link.evidence_id for link in base_fact.fact_evidence_links
            )
        evidence_ids.extend(i.input_evidence_id for i in justification_inputs)

        base_value = base_fact.value if base_fact is not None else "?"
        return NarrativeSentence(
            text=(
                f"Reported {label} was {base_value} in {fact.period}; "
                f"excluding {descriptions}, adjusted {label} was "
                f"{fact.value}, per the disclosure cited for this "
                f"adjustment."
            ),
            fact_ids=tuple(fact_ids),
            evidence_ids=tuple(evidence_ids),
        )

    @classmethod
    def render_company_period_narrative(
        cls, *, company_id: str, period: str
    ) -> list[NarrativeSentence]:
        """Every descriptive sentence for a company/period's NORMALIZED
        Facts, followed by every ADJUSTED Fact's own citation-gated
        commentary. Purely a read -- nothing is persisted.
        """

        sentences = [
            cls._render_normalized(fact)
            for fact in cls._current_facts_by_prefix(company_id, period, "fin.norm.")
        ]
        for fact in cls._current_facts_by_prefix(company_id, period, "fin.adj."):
            rendered = cls._render_adjusted(fact)
            if rendered is not None:
                sentences.append(rendered)
        return sentences
