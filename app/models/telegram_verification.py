from datetime import datetime
import sqlalchemy as sa
import sqlalchemy.orm as so
from app.models.base import BaseModel


class TelegramVerification(BaseModel):
    __tablename__ = 'telegram_verification'

    user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey('user.id'),
        nullable=False,
        index=True
    )
    verification_code: so.Mapped[str] = so.mapped_column(
        sa.String(20),
        nullable=False,
        unique=True,
        index=True
    )
    expires_at: so.Mapped[datetime] = so.mapped_column(
        sa.DateTime(timezone=True),
        nullable=False
    )
    verified: so.Mapped[bool] = so.mapped_column(
        sa.Boolean,
        default=False
    )

    # Relationships
    user: so.Mapped["User"] = so.relationship(back_populates="telegram_verifications")