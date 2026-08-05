"""Workspace, user, preferences and refresh sessions.

Every business table added from M2 onward carries ``workspace_id``. The single
workspace is seeded at install time; modelling the column now is what keeps
multi-tenancy from being a rewrite later (plan §2.1).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import IdMixin, TimestampMixin, UtcDateTime


def normalize_email(email: str) -> str:
    """Casefold and trim, for case-insensitive uniqueness and lookup.

    SQLite has no ``citext``, so the normalised form is stored explicitly and
    indexed. ``casefold`` rather than ``lower`` because it handles non-ASCII
    correctly; the address the user typed is preserved separately for display.
    """
    return email.strip().casefold()


class Workspace(IdMixin, TimestampMixin, Base):
    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")

    # Workspace-level, not per-user: it changes the Low stock KPI everyone sees
    # (design doc §13, "Units at or below this count are flagged low").
    low_stock_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=10)

    users: Mapped[list[User]] = relationship(back_populates="workspace")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Workspace {self.slug!r}>"


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("workspace_id", "email_normalized", name="workspace_email"),
        Index("ix_users_email_normalized", "email_normalized"),
    )

    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # As typed, for display. Never used for lookup.
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)

    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)

    # Free text, e.g. "Inventory lead". The prototype renders this as a disabled
    # input with "Roles are managed by your workspace admin", not a dropdown, so
    # it is a job title rather than a permission set. Authorisation is not
    # modelled in M1; every user in a workspace has the same access.
    role: Mapped[str] = mapped_column(String(80), nullable=False, default="Member")

    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    workspace: Mapped[Workspace] = relationship(back_populates="users")
    preferences: Mapped[UserPreferences | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    sessions: Mapped[list[AuthSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def initials(self) -> str:
        """ "Priya Mehta" -> "PM", for the header avatar."""
        parts = [p for p in self.full_name.split() if p]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.email_normalized!r}>"


class UserPreferences(TimestampMixin, Base):
    """Display settings (design doc §13, Settings → Display).

    Separate from ``users`` so a theme toggle does not write to the row holding
    the password hash. ``low_stock_threshold`` deliberately lives on Workspace.
    """

    __tablename__ = "user_preferences"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )

    theme: Mapped[str] = mapped_column(String(8), nullable=False, default="light")
    # "Comfortable/Compact, persisted per user" — design doc §15.
    table_density: Mapped[str] = mapped_column(String(12), nullable=False, default="comfortable")
    alert_on_stockout: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    user: Mapped[User] = relationship(back_populates="preferences")


class AuthSession(IdMixin, Base):
    """One refresh token, so logout can actually revoke.

    A bare JWT cannot be invalidated before it expires. The short-lived access
    token stays stateless; this row backs the long-lived refresh token, which is
    rotated on every use so a stolen token is single-use.
    """

    __tablename__ = "auth_sessions"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # SHA-256 of the token, never the token. A database leak must not hand over
    # usable sessions.
    refresh_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    issued_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")

    def is_usable(self, now: datetime) -> bool:
        return self.revoked_at is None and self.expires_at > now
