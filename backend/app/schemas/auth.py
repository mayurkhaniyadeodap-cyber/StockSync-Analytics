"""Request and response bodies for authentication."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# Same shape the prototype validates against client-side. Deliberately not
# pydantic's EmailStr, which would pull in the email-validator package for no
# benefit here: this is an internal tool where addresses are issued by an admin,
# not self-registered, and the authoritative check is whether the row exists.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class LoginRequest(BaseModel):
    email: str = Field(max_length=320, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=1, max_length=1024)
    # "Keep me signed in" — design doc §6.
    remember_me: bool = False


class ForgotPasswordRequest(BaseModel):
    """Just the address. Nothing about the account is echoed back."""

    # The same pattern the login body uses — see EMAIL_PATTERN above for why
    # this is not pydantic's EmailStr.
    email: str = Field(max_length=320, pattern=EMAIL_PATTERN)


class ResetPasswordRequest(BaseModel):
    """The token from the link, and the password to set.

    The token is not length-bounded here: it is a signed JWT whose size depends
    on the secret and the claims, and a bound that fits today would reject a
    perfectly good token after a claim is added. `verify` rejects anything that
    does not decode, which is the real check.
    """

    token: str = Field(min_length=1)
    password: str = Field(min_length=1, max_length=1024)


class EmailChangeRequest(BaseModel):
    """Move the login identity. The current password re-proves the session."""

    new_email: str = Field(max_length=320, pattern=EMAIL_PATTERN)
    current_password: str = Field(min_length=1, max_length=1024)


class ConfirmEmailChangeRequest(BaseModel):
    """The token from the verification link, and nothing else.

    No password here: the link is opened in whichever browser reads the new
    mailbox, which is usually not one that is signed in.
    """

    token: str = Field(min_length=1)


class ChangePasswordRequest(BaseModel):
    """Current and new. The confirmation field is checked in the browser.

    A mismatch between two identical inputs is a typing mistake, not a security
    decision, and sending both would give two places an opinion about what
    "they match" means.
    """

    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class WorkspaceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    timezone: str
    currency: str
    low_stock_threshold: int


class PreferencesPayload(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    theme: str = "light"
    table_density: str = "comfortable"
    alert_on_stockout: bool = True


class CurrentUser(BaseModel):
    """Everything the app shell needs to render: header, avatar, preferences."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    role: str
    timezone: str
    initials: str
    workspace: WorkspaceSummary
    preferences: PreferencesPayload

    #: When the current access token stops being accepted, so the client can
    #: rotate the session *before* a request fails rather than after. It is a
    #: timestamp, not a credential: the token itself stays in the httpOnly
    #: cookie and is never serialised.
    #:
    #: Null on the endpoints that neither issue a token nor read one's claims —
    #: the profile and preferences patches. A client that gets null falls back
    #: to recovering from the 401, which is the same path a clock skew takes.
    access_expires_at: datetime | None = None


class PreferencesUpdate(BaseModel):
    """All fields optional — the Display panel saves one setting at a time."""

    theme: str | None = Field(default=None, pattern="^(light|dark)$")
    table_density: str | None = Field(default=None, pattern="^(comfortable|compact)$")
    alert_on_stockout: bool | None = None

    #: Workspace-scoped, unlike the three above, because it changes the Low
    #: stock figure everyone in the workspace sees (design doc S13). It is
    #: accepted here because that is the panel it belongs to; the UI says who
    #: it affects.
    low_stock_threshold: int | None = Field(default=None, ge=0, le=100_000)


class ProfileUpdate(BaseModel):
    """The editable half of Settings -> Profile.

    Email and role are absent on purpose. Email is the login identity, so
    changing it is an authentication change rather than a profile edit; role is
    a job title the workspace admin sets, which the prototype has always shown
    as a disabled field. Both are displayed, neither is writable here.
    """

    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
