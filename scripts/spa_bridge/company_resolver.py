"""Fail-closed SPA company -> M1 Company resolution.

M1's own architecture makes company identity an explicit admin decision:
``Company`` requires a ``ticker_id`` into the *legacy* ``Ticker`` table
plus a human-supplied ISIN (``ResearchCommandService.create_company``),
and the Document Library itself has no company-creation path at all.

This resolver only ever looks a company up. It never creates a Ticker or
a Company. A SPA Universe company with no matching legacy Ticker symbol,
or a Ticker with no M1 Company registered against it yet, resolves to
``None`` -- the caller reports that row as unresolved and leaves
registration to the existing admin endpoint
(``POST /api/admin/research/companies``), exactly as approved in the
bridge design.
"""

from __future__ import annotations

import sqlalchemy as sa

from app import db
from app.models import Company, Ticker


def resolve_company_id(nse_symbol: str, bse_security_code: str) -> str | None:
    """Resolve via the legacy Ticker symbol, trying NSE then BSE.

    Both SPA's Universe tab and the legacy ``Ticker`` table use bare
    exchange trading symbols, so this is a direct equality match -- no
    normalization is applied, so a blank or whitespace-only code is
    skipped rather than matched against a blank Ticker symbol.
    """

    for candidate in (nse_symbol, bse_security_code):
        symbol = (candidate or "").strip()
        if not symbol:
            continue
        ticker = db.session.scalar(
            sa.select(Ticker).where(Ticker.symbol == symbol)
        )
        if ticker is None:
            continue
        company = db.session.scalar(
            sa.select(Company).where(Company.ticker_id == ticker.id)
        )
        if company is not None:
            return company.id
    return None
