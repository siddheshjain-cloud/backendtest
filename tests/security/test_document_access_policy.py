"""Plan 4 Task 4: document access and derived-rights policy matrix.

The suite locks the non-entitlement document rights policy: public
accessibility is not redistribution, premium tier is not a rights override,
provider/admin management visibility is not consumer distribution, LINK_ONLY
exposes only official link metadata, and archived or inaccessible rows are
non-revealing.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone
from itertools import product

import pytest

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
from app.policies.document_access import (
    DocumentAccessDecision,
    DocumentAccessPolicy,
)
from app.services.entitlement_service import ResearchAccessContext


PUBLIC_DOCUMENT_FIELDS = frozenset(
    {
        "id",
        "document_type",
        "title",
        "document_date",
        "original_published_date",
        "original_published_at",
        "original_published_at_precision",
        "reporting_period",
        "publisher_name",
        "publisher_reference",
        "original_source_url",
    }
)

MANAGED_DOCUMENT_FIELDS = frozenset(
    {
        "id",
        "document_type",
        "title",
        "document_date",
        "supersedes_document_id",
        "original_published_date",
        "original_published_at",
        "original_published_at_precision",
        "reporting_period",
        "publisher_name",
        "publisher_reference",
        "original_source_url",
        "discovery_source_type",
        "discovery_source_reference",
        "source_access",
        "acquisition_method",
        "distribution_status",
        "ingestion_status",
        "mime_type",
        "file_size_bytes",
        "provided_by_user_id",
        "distribution_basis",
        "rights_verified_by_user_id",
        "rights_verified_at",
        "created_by_user_id",
        "created_at",
        "updated_at",
        "archived_at",
        "content_hash_sha256",
        "metadata_fingerprint",
    }
)

SOURCE_ACCESS_VALUES = (
    SourceAccess.PUBLIC,
    SourceAccess.RESTRICTED,
    SourceAccess.UNKNOWN,
)

DISTRIBUTION_STATUS_VALUES = (
    DistributionStatus.UNKNOWN,
    DistributionStatus.LINK_ONLY,
    DistributionStatus.PRIVATE_LIBRARY,
    DistributionStatus.APP_DISTRIBUTABLE,
)


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
    provided_by_user_id: str | None = None,
    source_access: str = SourceAccess.PUBLIC,
    distribution_status: str = DistributionStatus.LINK_ONLY,
    title: str = "Public linked report",
    original_source_url: str = "https://publisher.example/report",
    storage_key: str = "private/object-store/key",
    storage_provider: str = "object-store",
    archived_at: datetime | None = None,
    **overrides: object,
) -> Document:
    values: dict[str, object] = {
        "document_type": DocumentType.ANNUAL_REPORT,
        "title": title,
        "document_date": date(2026, 8, 31),
        "discovery_source_type": DiscoverySourceType.OFFICIAL_SITE,
        "source_access": source_access,
        "acquisition_method": AcquisitionMethod.MANUAL_REFERENCE,
        "distribution_status": distribution_status,
        "ingestion_status": IngestionStatus.DISCOVERED,
        "original_source_url": original_source_url,
        "storage_provider": storage_provider,
        "storage_key": storage_key,
        "metadata_fingerprint": "a" * 64,
        "created_by_user_id": created_by_user_id,
        "provided_by_user_id": provided_by_user_id,
        "archived_at": archived_at,
    }
    values.update(overrides)
    document = Document(**values)
    db.session.add(document)
    db.session.commit()
    return document


def test_decision_is_immutable(app, admin_user):
    document = _document(created_by_user_id=admin_user.id)

    decision = DocumentAccessPolicy.evaluate(
        document,
        _context("free-user"),
    )

    assert isinstance(decision, DocumentAccessDecision)
    assert isinstance(decision.metadata_fields, frozenset)
    with pytest.raises(FrozenInstanceError):
        decision.visible = False
    with pytest.raises(FrozenInstanceError):
        decision.may_contribute_to_aggregate = True


@pytest.mark.parametrize("source_access", SOURCE_ACCESS_VALUES)
@pytest.mark.parametrize("distribution_status", DISTRIBUTION_STATUS_VALUES)
@pytest.mark.parametrize("tier", [ResearchTier.FREE, ResearchTier.PREMIUM])
def test_unrelated_consumer_matrix_never_lets_tier_override_rights(
    app,
    admin_user,
    premium_user,
    source_access,
    distribution_status,
    tier,
):
    document = _document(
        created_by_user_id=admin_user.id,
        provided_by_user_id=None,
        source_access=source_access,
        distribution_status=distribution_status,
    )
    context = _context(premium_user.id, tier=tier)

    decision = DocumentAccessPolicy.evaluate(document, context)

    expected_visible = (
        source_access == SourceAccess.PUBLIC
        and distribution_status
        in (DistributionStatus.LINK_ONLY, DistributionStatus.APP_DISTRIBUTABLE)
    )
    expected_contribute = (
        expected_visible
        and distribution_status == DistributionStatus.APP_DISTRIBUTABLE
    )

    assert decision.visible is expected_visible
    assert (
        decision.may_contribute_to_aggregate
        is expected_contribute
    )

    if expected_visible:
        assert decision.metadata_fields == PUBLIC_DOCUMENT_FIELDS
    else:
        assert decision.metadata_fields == frozenset()
        assert DocumentAccessPolicy.project(document, decision) == {}


def test_public_link_only_is_visible_but_not_distributable(
    app, admin_user, premium_user
):
    document = _document(
        created_by_user_id=admin_user.id,
        source_access=SourceAccess.PUBLIC,
        distribution_status=DistributionStatus.LINK_ONLY,
        original_source_url="https://publisher.example/official",
        storage_key="private/storage/never-returned",
    )

    decision = DocumentAccessPolicy.evaluate(
        document,
        _context(premium_user.id, tier=ResearchTier.PREMIUM),
    )
    projection = DocumentAccessPolicy.project(document, decision)

    assert decision.visible is True
    assert decision.may_contribute_to_aggregate is False
    assert set(projection) == PUBLIC_DOCUMENT_FIELDS
    assert projection["original_source_url"] == (
        "https://publisher.example/official"
    )
    assert "storage_key" not in projection
    assert "storage_provider" not in projection
    assert "content_hash_sha256" not in projection
    assert "metadata_fingerprint" not in projection


@pytest.mark.parametrize("source_access", SOURCE_ACCESS_VALUES)
@pytest.mark.parametrize("distribution_status", DISTRIBUTION_STATUS_VALUES)
def test_provider_can_manage_private_metadata_without_storage_leak(
    app,
    admin_user,
    user_factory,
    source_access,
    distribution_status,
):
    provider = user_factory(email="provider@example.com")
    verified_at = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    document = _document(
        created_by_user_id=admin_user.id,
        provided_by_user_id=provider.id,
        source_access=source_access,
        distribution_status=distribution_status,
        acquisition_method=AcquisitionMethod.USER_UPLOAD,
        distribution_basis="Licence on file",
        rights_verified_by_user_id=admin_user.id,
        rights_verified_at=verified_at,
        content_hash_sha256="b" * 64,
        metadata_fingerprint="c" * 64,
    )

    decision = DocumentAccessPolicy.evaluate(
        document,
        _context(provider.id),
    )
    projection = DocumentAccessPolicy.project(document, decision)

    assert decision.visible is True
    expected_contribute = (
        distribution_status == DistributionStatus.APP_DISTRIBUTABLE
        or source_access != SourceAccess.PUBLIC
        or distribution_status
        in (DistributionStatus.PRIVATE_LIBRARY, DistributionStatus.UNKNOWN)
    )
    assert decision.may_contribute_to_aggregate is expected_contribute
    assert set(projection) == MANAGED_DOCUMENT_FIELDS
    assert projection["provided_by_user_id"] == provider.id
    assert projection["rights_verified_by_user_id"] == admin_user.id
    assert projection["content_hash_sha256"] == "b" * 64
    assert projection["metadata_fingerprint"] == "c" * 64
    assert "storage_key" not in projection
    assert "storage_provider" not in projection


def test_admin_private_management_does_not_make_link_only_distributable(
    app, admin_user
):
    document = _document(
        created_by_user_id=admin_user.id,
        source_access=SourceAccess.PUBLIC,
        distribution_status=DistributionStatus.LINK_ONLY,
        original_source_url="https://publisher.example/official",
    )

    decision = DocumentAccessPolicy.evaluate(
        document,
        _context(admin_user.id, is_admin=True),
    )
    projection = DocumentAccessPolicy.project(document, decision)

    assert decision.visible is True
    assert decision.may_contribute_to_aggregate is False
    assert projection["distribution_status"] == DistributionStatus.LINK_ONLY
    assert "storage_key" not in projection
    assert "storage_provider" not in projection


def test_archived_document_is_not_visible_to_any_policy_context(
    app, admin_user, premium_user, user_factory
):
    provider = user_factory(email="archived-provider@example.com")
    document = _document(
        created_by_user_id=admin_user.id,
        provided_by_user_id=provider.id,
        source_access=SourceAccess.PUBLIC,
        distribution_status=DistributionStatus.APP_DISTRIBUTABLE,
        archived_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
    )
    contexts = [
        _context(premium_user.id, tier=ResearchTier.PREMIUM),
        _context(provider.id),
        _context(admin_user.id, is_admin=True),
    ]

    for context in contexts:
        decision = DocumentAccessPolicy.evaluate(document, context)
        assert decision.visible is False
        assert decision.may_contribute_to_aggregate is False
        assert decision.metadata_fields == frozenset()
        assert DocumentAccessPolicy.project(document, decision) == {}


@pytest.mark.parametrize("source_access", SOURCE_ACCESS_VALUES)
@pytest.mark.parametrize("distribution_status", DISTRIBUTION_STATUS_VALUES)
def test_no_projection_ever_contains_storage_fields(
    app,
    admin_user,
    premium_user,
    user_factory,
    source_access,
    distribution_status,
):
    provider = user_factory(email="no-storage-provider@example.com")
    document = _document(
        created_by_user_id=admin_user.id,
        provided_by_user_id=provider.id,
        source_access=source_access,
        distribution_status=distribution_status,
        storage_provider="object-store",
        storage_key="opaque/storage/key",
    )
    contexts = [
        _context(premium_user.id, tier=ResearchTier.PREMIUM),
        _context(provider.id),
        _context(admin_user.id, is_admin=True),
    ]

    for context in contexts:
        decision = DocumentAccessPolicy.evaluate(document, context)
        projection = DocumentAccessPolicy.project(document, decision)
        assert "storage_key" not in projection
        assert "storage_provider" not in projection
