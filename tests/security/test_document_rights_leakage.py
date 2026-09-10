"""Plan 4 Task 4: derived-rights and aggregation leakage regression.

Restricted contributions must never influence an unrelated consumer's
counts, ranges, or differences. Provider/admin private views may aggregate
only within that same private context, and every projection omits storage
and private file references.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from app import db
from app.models.document import (
    AcquisitionMethod,
    DiscoverySourceType,
    DistributionStatus,
    Document,
    DocumentType,
    IngestionStatus,
    SourceAccess,
)
from app.models.research_types import ResearchTier
from app.policies.document_access import DocumentAccessPolicy
from app.services.entitlement_service import ResearchAccessContext


def _context(
    user_id: str,
    *,
    is_admin: bool = False,
    tier: str = ResearchTier.FREE,
) -> ResearchAccessContext:
    return ResearchAccessContext(user_id, is_admin, tier)


def _document(
    *,
    created_by_user_id: str,
    provided_by_user_id: str | None,
    title: str,
    document_date: date,
    source_access: str,
    distribution_status: str,
    file_size_bytes: int | None,
) -> Document:
    values: dict[str, object] = {
        "document_type": DocumentType.INSTITUTIONAL_RESEARCH,
        "title": title,
        "document_date": document_date,
        "discovery_source_type": DiscoverySourceType.USER,
        "source_access": source_access,
        "acquisition_method": (
            AcquisitionMethod.USER_UPLOAD
            if provided_by_user_id is not None
            else AcquisitionMethod.MANUAL_REFERENCE
        ),
        "distribution_status": distribution_status,
        "ingestion_status": IngestionStatus.DISCOVERED,
        "original_source_url": (
            "https://publisher.example/public"
            if source_access == SourceAccess.PUBLIC
            else None
        ),
        "metadata_fingerprint": f"{title}:{document_date.isoformat()}"[:64],
        "provided_by_user_id": provided_by_user_id,
        "file_size_bytes": file_size_bytes,
        "created_by_user_id": created_by_user_id,
    }
    if distribution_status == DistributionStatus.APP_DISTRIBUTABLE:
        values.update(
            {
                "distribution_basis": "Independently verified public licence",
                "rights_verified_by_user_id": created_by_user_id,
                "rights_verified_at": datetime(
                    2026, 9, 1, tzinfo=timezone.utc
                ),
            }
        )
    document = Document(**values)
    db.session.add(document)
    db.session.commit()
    return document


def _seed_documents(app, admin_user, provider_user) -> list[Document]:
    return [
        _document(
            created_by_user_id=admin_user.id,
            provided_by_user_id=None,
            title="PUBLIC-AGGREGATE-SENTINEL",
            document_date=date(2026, 9, 1),
            source_access=SourceAccess.PUBLIC,
            distribution_status=DistributionStatus.APP_DISTRIBUTABLE,
            file_size_bytes=100,
        ),
        _document(
            created_by_user_id=admin_user.id,
            provided_by_user_id=None,
            title="PUBLIC-LINK-ONLY-SENTINEL",
            document_date=date(2026, 8, 1),
            source_access=SourceAccess.PUBLIC,
            distribution_status=DistributionStatus.LINK_ONLY,
            file_size_bytes=200,
        ),
        _document(
            created_by_user_id=admin_user.id,
            provided_by_user_id=provider_user.id,
            title="RESTRICTED-PRIVATE-SENTINEL",
            document_date=date(1900, 1, 1),
            source_access=SourceAccess.RESTRICTED,
            distribution_status=DistributionStatus.PRIVATE_LIBRARY,
            file_size_bytes=999_999,
        ),
        _document(
            created_by_user_id=admin_user.id,
            provided_by_user_id=provider_user.id,
            title="RESTRICTED-DISTRIBUTABLE-SENTINEL",
            document_date=date(2100, 1, 1),
            source_access=SourceAccess.RESTRICTED,
            distribution_status=DistributionStatus.APP_DISTRIBUTABLE,
            file_size_bytes=888_888,
        ),
    ]


def _contributions(
    documents: list[Document], context: ResearchAccessContext
) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for document in documents:
        decision = DocumentAccessPolicy.evaluate(document, context)
        if decision.visible and decision.may_contribute_to_aggregate:
            output.append(DocumentAccessPolicy.project(document, decision))
    return output


def test_unrelated_premium_aggregation_excludes_every_restricted_contribution(
    app, admin_user, premium_user, user_factory
):
    provider = user_factory(email="leak-provider@example.com")
    documents = _seed_documents(app, admin_user, provider)
    context = _context(
        premium_user.id,
        tier=ResearchTier.PREMIUM,
    )

    contributions = _contributions(documents, context)

    assert len(contributions) == 1
    assert contributions[0]["title"] == "PUBLIC-AGGREGATE-SENTINEL"
    assert {
        item["document_date"] for item in contributions
    } == {"2026-09-01"}

    serialized = str(contributions)
    assert "RESTRICTED-PRIVATE-SENTINEL" not in serialized
    assert "RESTRICTED-DISTRIBUTABLE-SENTINEL" not in serialized
    assert "PUBLIC-LINK-ONLY-SENTINEL" not in serialized


def test_restricted_values_cannot_shift_exposed_range_or_difference(
    app, admin_user, premium_user, user_factory
):
    provider = user_factory(email="range-provider@example.com")
    documents = _seed_documents(app, admin_user, provider)
    context = _context(
        premium_user.id,
        tier=ResearchTier.PREMIUM,
    )

    contributions = _contributions(documents, context)
    exposed_dates = sorted(item["document_date"] for item in contributions)

    assert exposed_dates == ["2026-09-01"]
    assert exposed_dates[0] == exposed_dates[-1]
    assert len(exposed_dates) == 1
    assert "1900-01-01" not in exposed_dates
    assert "2100-01-01" not in exposed_dates


def test_provider_private_view_keeps_restricted_values_in_private_context(
    app, admin_user, premium_user, user_factory
):
    provider = user_factory(email="owner-provider@example.com")
    documents = _seed_documents(app, admin_user, provider)
    context = _context(provider.id)

    contributions = _contributions(documents, context)
    titles = {item["title"] for item in contributions}

    assert titles == {
        "PUBLIC-AGGREGATE-SENTINEL",
        "RESTRICTED-PRIVATE-SENTINEL",
        "RESTRICTED-DISTRIBUTABLE-SENTINEL",
    }
    assert "PUBLIC-LINK-ONLY-SENTINEL" not in titles
    for item in contributions:
        assert "storage_key" not in item
        assert "storage_provider" not in item


def test_unrelated_consumer_projection_has_no_private_file_metadata(
    app, admin_user, premium_user, user_factory
):
    provider = user_factory(email="private-metadata-provider@example.com")
    documents = _seed_documents(app, admin_user, provider)
    context = _context(
        premium_user.id,
        tier=ResearchTier.PREMIUM,
    )

    for document in documents:
        decision = DocumentAccessPolicy.evaluate(document, context)
        projection = DocumentAccessPolicy.project(document, decision)
        assert "storage_key" not in projection
        assert "storage_provider" not in projection
        assert "file_size_bytes" not in projection
        assert "content_hash_sha256" not in projection
        assert "metadata_fingerprint" not in projection
        assert "provided_by_user_id" not in projection

