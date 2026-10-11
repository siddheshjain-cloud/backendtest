"""Research Coverage & Fact Intelligence, Slice 4: model-level tests for
``ResearchProposition``, ``PropositionStageType``, and ``PropositionLink``.

Locks: immutability on all three models, and the database-enforced
"exactly one source" check constraint on ``PropositionLink`` (the same
NULL-safe ``(x IS NOT NULL) != (y IS NOT NULL)`` pattern
``FactDerivationInput`` already uses).
"""

from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa

from app import db
from app.models import Company
from app.models.document import (
    AcquisitionMethod,
    DiscoverySourceType,
    DistributionStatus,
    DocumentType,
    IngestionStatus,
    SourceAccess,
)
from app.models.research_coverage import (
    PropositionLink,
    PropositionStageType,
    ResearchProposition,
)
from app.services.document_library_service import DocumentLibraryService
from app.services.research_brain_service import ResearchBrainService


@pytest.fixture
def company(ticker_factory):
    ticker = ticker_factory(symbol="UNOMINDA", instrument_token=2)
    company = Company(
        ticker_id=ticker.id,
        legal_name="UNO Minda Limited",
        isin="INE405E01023",
    )
    db.session.add(company)
    db.session.commit()
    return company


@pytest.fixture
def document(admin_user, company):
    payload = {
        "document": {
            "document_type": DocumentType.REG30_ATTACHMENT,
            "title": "UNO_REG30_CAPACITY_ADDITION_TEST",
            "document_date": "2026-07-07",
            "reporting_period": None,
            "publisher_name": "UNO Minda Limited",
            "original_source_url": "https://example.in/unominda/reg30-capacity",
            "discovery_source_type": DiscoverySourceType.EXCHANGE,
            "source_access": SourceAccess.PUBLIC,
            "acquisition_method": AcquisitionMethod.MANUAL_REFERENCE,
            "distribution_status": DistributionStatus.LINK_ONLY,
            "ingestion_status": IngestionStatus.DISCOVERED,
        },
        "company_links": [{"company_id": company.id, "is_primary": True}],
    }
    return DocumentLibraryService.create_document(payload, admin_user.id)


@pytest.fixture
def evidence_and_fact(admin_user, company, document):
    run = ResearchBrainService.create_extraction_run(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    evidence = ResearchBrainService.record_evidence(
        extraction_run_id=run.id,
        document_id=document.id,
        text_snippet="Board approved a greenfield capacity expansion.",
        created_by_user_id=admin_user.id,
    )
    fact = ResearchBrainService.record_fact(
        company_id=company.id,
        fact_type="capacity_expansion_test",
        value_type="TEXT",
        value="Board approved a greenfield capacity expansion.",
        period="Q1 FY2027",
        as_of_date=date(2026, 7, 7),
        evidence_ids=[evidence.id],
        created_by_user_id=admin_user.id,
    )
    return fact, evidence


@pytest.fixture
def proposition(admin_user, company):
    prop = ResearchProposition(
        company_id=company.id,
        title="Test capacity proposition",
        description="A test longitudinal thread.",
        created_by_user_id=admin_user.id,
    )
    db.session.add(prop)
    db.session.commit()
    return prop


@pytest.fixture
def stage_type(app):
    stage = PropositionStageType(
        code="COMMITTED",
        name="Committed",
        description="Budget/JV formally approved.",
    )
    db.session.add(stage)
    db.session.commit()
    return stage


def test_research_proposition_cannot_be_updated_or_deleted(proposition):
    proposition.title = "edited"
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    db.session.delete(db.session.get(ResearchProposition, proposition.id))
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()


def test_proposition_stage_type_cannot_be_updated_or_deleted(stage_type):
    stage_type.name = "edited"
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    db.session.delete(db.session.get(PropositionStageType, stage_type.id))
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()


def test_proposition_link_requires_exactly_one_source(
    admin_user, proposition, stage_type, document, evidence_and_fact
):
    fact, _ = evidence_and_fact

    # Neither set.
    bad_neither = PropositionLink(
        proposition_id=proposition.id,
        stage_type_id=stage_type.id,
        document_id=document.id,
        created_by_user_id=admin_user.id,
    )
    db.session.add(bad_neither)
    with pytest.raises(sa.exc.IntegrityError):
        db.session.commit()
    db.session.rollback()

    # Both set.
    bad_both = PropositionLink(
        proposition_id=proposition.id,
        stage_type_id=stage_type.id,
        document_id=document.id,
        fact_id=fact.id,
        candidate_finding_id="00000000-0000-0000-0000-000000000000",
        created_by_user_id=admin_user.id,
    )
    db.session.add(bad_both)
    with pytest.raises(sa.exc.IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_proposition_link_cannot_be_updated_or_deleted(
    admin_user, proposition, stage_type, document, evidence_and_fact
):
    fact, _ = evidence_and_fact
    link = PropositionLink(
        proposition_id=proposition.id,
        stage_type_id=stage_type.id,
        document_id=document.id,
        fact_id=fact.id,
        as_of_date=date(2026, 7, 7),
        created_by_user_id=admin_user.id,
    )
    db.session.add(link)
    db.session.commit()

    link.stage_note = "edited"
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    db.session.delete(db.session.get(PropositionLink, link.id))
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()
