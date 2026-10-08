"""Phase 3 Slice E -- ``ForecastRevisionAssumption``.

Pure join table recording exactly which ``ForecastAssumption`` rows
(Slice D) a given scenario-tagged ``ForecastRevision`` was built from --
the citation trail ``ForecastEngineService.create_scenario_forecast``
writes once and never edits, the same row-per-citation idiom as every
other join table in this codebase (``CycleAssessmentObservation``,
``InvestmentHypothesisCitation``). No discriminated union is needed here
-- both sides are always present, so this is a plain two-FK join, unlike
those two.
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlalchemy.orm as so

from app.models.base import BaseModel


class ForecastRevisionAssumption(BaseModel):
    __tablename__ = "forecast_revision_assumption"

    forecast_revision_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("forecast_revision.id"), nullable=False
    )
    forecast_assumption_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("forecast_assumption.id"), nullable=False
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "forecast_revision_id", "forecast_assumption_id",
            name="uq_forecast_revision_assumption_pair",
        ),
    )

    forecast_revision: so.Mapped["ForecastRevision"] = so.relationship(
        "ForecastRevision", viewonly=True
    )
    forecast_assumption: so.Mapped["ForecastAssumption"] = so.relationship(
        "ForecastAssumption", viewonly=True
    )

    def __repr__(self) -> str:
        return (
            f"<ForecastRevisionAssumption revision={self.forecast_revision_id} "
            f"assumption={self.forecast_assumption_id}>"
        )


def _reject_immutable_update(_mapper, _connection, target) -> None:
    raise sa.exc.InvalidRequestError(
        f"{type(target).__name__} is immutable after insertion"
    )


def _reject_immutable_delete(_mapper, _connection, target) -> None:
    raise sa.exc.InvalidRequestError(
        f"{type(target).__name__} is immutable and cannot be deleted"
    )


sa.event.listen(
    ForecastRevisionAssumption, "before_update", _reject_immutable_update
)
sa.event.listen(
    ForecastRevisionAssumption, "before_delete", _reject_immutable_delete
)
