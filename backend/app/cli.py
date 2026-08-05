"""Administrative commands.

    python -m app.cli seed                                    # default administrator
    python -m app.cli seed --email you@deodap.in --name "Your Name"
    python -m app.cli set-password --email you@deodap.in
    python -m app.cli check-inventory                          # report inconsistencies
    python -m app.cli check-inventory --repair                 # and fix them

There is no self-registration: this is an internal tool where accounts are
issued.

Run with no arguments, ``seed`` creates the default administrator below so a
freshly migrated database is always signed into with the documented
credentials. That password is public — it is in this file and in the README —
so it is a development convenience only. Change it with ``set-password``
before any deployment that is reachable by anyone else.

Passwords are otherwise prompted for interactively rather than passed as
arguments, so they never land in shell history or the process list. For
scripted setup pass --password-env NAME and put the value in that environment
variable, which is still better than an argv flag.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import get_session_factory
from app.models import InventoryItem, User, UserPreferences, Workspace, normalize_email

DEFAULT_WORKSPACE_NAME = "Deodap Retail"
DEFAULT_WORKSPACE_SLUG = "deodap"
MIN_PASSWORD_LENGTH = 12

# The administrator issued to every new database. Documented in the README, so
# it is a known credential by design and carries no secrecy — see the module
# docstring.
DEFAULT_ADMIN_EMAIL = "admin@deodap.in"
DEFAULT_ADMIN_NAME = "Administrator"
DEFAULT_ADMIN_ROLE = "Admin"
DEFAULT_ADMIN_PASSWORD = "StockSync@123"  # noqa: S105


def _read_password(
    password_env: str | None = None,
    confirm: bool = True,
    *,
    default: str | None = None,
) -> str:
    """Read a password without it ever appearing in argv.

    Order: the named environment variable, then ``default`` (supplied only when
    seeding the documented default administrator), then an interactive prompt.
    There is deliberately no "detect a non-interactive terminal and generate
    one" path — ``sys.stdin.isatty()`` is not reliable across Windows terminals
    (it reports True under redirection in Git Bash), so that branch would hang
    precisely where automation needs it not to.
    """
    if password_env:
        value = os.environ.get(password_env)
        if not value:
            raise SystemExit(f"Environment variable {password_env} is unset or empty.")
        if len(value) < MIN_PASSWORD_LENGTH:
            raise SystemExit(f"{password_env} must be at least {MIN_PASSWORD_LENGTH} characters.")
        return value

    if default is not None:
        return default

    while True:
        password = getpass.getpass("Password: ")
        if len(password) < MIN_PASSWORD_LENGTH:
            print(f"Too short — use at least {MIN_PASSWORD_LENGTH} characters.")
            continue
        if confirm and password != getpass.getpass("Confirm password: "):
            print("Those didn't match. Try again.")
            continue
        return password


def _get_or_create_workspace(db: Session) -> Workspace:
    workspace = db.scalars(select(Workspace).limit(1)).first()
    if workspace is not None:
        return workspace
    workspace = Workspace(name=DEFAULT_WORKSPACE_NAME, slug=DEFAULT_WORKSPACE_SLUG)
    db.add(workspace)
    db.flush()
    print(f"Created workspace {workspace.name!r}.")
    return workspace


def seed(
    email: str = DEFAULT_ADMIN_EMAIL,
    full_name: str = DEFAULT_ADMIN_NAME,
    role: str = DEFAULT_ADMIN_ROLE,
    password_env: str | None = None,
) -> int:
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        print(f"'{email}' doesn't look like an email address.", file=sys.stderr)
        return 2

    normalized = normalize_email(email)
    # Only the documented administrator gets a built-in password; any other
    # account still has to supply one, so this is a fixed bootstrap rather than
    # a general "seed without a password" escape hatch.
    is_default_admin = normalized == DEFAULT_ADMIN_EMAIL

    with get_session_factory()() as db:
        workspace = _get_or_create_workspace(db)

        existing = db.scalars(
            select(User).where(
                User.workspace_id == workspace.id, User.email_normalized == normalized
            )
        ).first()
        if existing is not None:
            if is_default_admin:
                # Re-running the bootstrap on an already-seeded database is not
                # a failure — reset-db chains migrate and seed unconditionally.
                print(f"{existing.email} already exists. Use set-password to change its password.")
                return 0
            print(f"{email} already exists. Use set-password to change its password.")
            return 1

        password = _read_password(
            password_env, default=DEFAULT_ADMIN_PASSWORD if is_default_admin else None
        )
        user = User(
            workspace_id=workspace.id,
            email=email.strip(),
            email_normalized=normalized,
            password_hash=hash_password(password),
            full_name=full_name,
            role=role,
            timezone=workspace.timezone,
            is_active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserPreferences(user_id=user.id))
        db.commit()

        print(f"Created {user.full_name} <{user.email}> in workspace {workspace.name!r}.")
        if is_default_admin and password_env is None:
            print(f"Password: {DEFAULT_ADMIN_PASSWORD} — change it with set-password before use.")
    return 0


def set_password(email: str, password_env: str | None = None) -> int:
    with get_session_factory()() as db:
        user = db.scalars(
            select(User).where(User.email_normalized == normalize_email(email))
        ).first()
        if user is None:
            print(f"No user with email {email}.", file=sys.stderr)
            return 1

        user.password_hash = hash_password(_read_password(password_env))
        # Force a fresh sign-in everywhere: a password change that leaves old
        # sessions alive is not a password change.
        for session in user.sessions:
            if session.revoked_at is None:
                session.revoked_at = user.updated_at
        db.commit()
        print(f"Password updated for {user.email}. All existing sessions were signed out.")
    return 0


def check_inventory(repair: bool = False) -> int:
    """Report rows whose two quantity fields disagree, and optionally fix them.

    ``quantity_on_hand`` and ``total_qty`` are written from one cell and read by
    different screens: the Dashboard card sums the first, Analytics sums the
    second. When they disagree the two screens show different totals for the
    same workspace, which is what this finds.

    The current importer cannot produce a disagreement — it reconciles every row
    on the way in — so anything reported here was written by an earlier version.

    **Reports by default and changes nothing.** Correcting stock figures is not
    something that should happen as a side effect of running a check, so the
    repair is a separate, explicit request. Re-importing the affected sheet is
    the better fix where the file still exists: it restores every column from
    the source rather than reconciling two of them.
    """
    with get_session_factory()() as db:
        rows = list(
            db.scalars(
                select(InventoryItem)
                .where(InventoryItem.quantity_on_hand != InventoryItem.total_qty)
                .order_by(InventoryItem.sku_normalized)
            )
        )

        if not rows:
            print("Every SKU's quantity_on_hand matches its total_qty.")
            return 0

        drift = sum(item.total_qty - item.quantity_on_hand for item in rows)
        print(f"{len(rows)} SKU(s) have quantity_on_hand != total_qty.")
        print(f"Dashboard 'Total quantity' is understating Analytics by {drift:,} units.\n")
        print(f"  {'SKU':<44} {'quantity_on_hand':>17} {'total_qty':>10} {'batch':>6}")
        for item in rows[:20]:
            print(
                f"  {item.sku[:44]:<44} {item.quantity_on_hand:>17,} "
                f"{item.total_qty:>10,} {item.source_batch_id or '-'!s:>6}"
            )
        if len(rows) > 20:
            print(f"  ... and {len(rows) - 20} more")

        if not repair:
            print(
                "\nNothing was changed. Re-import the sheets these came from, or run again"
                "\nwith --repair to set quantity_on_hand from the sheet's Total Qty."
            )
            return 1

        for item in rows:
            item.quantity_on_hand = item.total_qty
            item.quantity_imported = item.total_qty
        db.commit()
        print(f"\nRepaired {len(rows)} row(s): quantity_on_hand now matches total_qty.")
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    seed_parser = sub.add_parser(
        "seed",
        help="create the workspace and the default administrator (or a named user)",
    )
    # All optional: with none of them supplied this seeds the default
    # administrator. --name stays mandatory for any other address.
    seed_parser.add_argument("--email", default=DEFAULT_ADMIN_EMAIL)
    seed_parser.add_argument("--name", dest="full_name", default=None)
    seed_parser.add_argument("--role", default=None)
    seed_parser.add_argument(
        "--password-env",
        metavar="NAME",
        help="read the password from this environment variable instead of prompting",
    )

    pw_parser = sub.add_parser("set-password", help="change a user's password")
    pw_parser.add_argument("--email", required=True)
    pw_parser.add_argument(
        "--password-env",
        metavar="NAME",
        help="read the password from this environment variable instead of prompting",
    )

    check_parser = sub.add_parser(
        "check-inventory",
        help="report SKUs whose quantity_on_hand and total_qty disagree",
    )
    check_parser.add_argument(
        "--repair",
        action="store_true",
        help="set quantity_on_hand from the sheet's Total Qty for the rows listed",
    )

    args = parser.parse_args(argv)

    if args.command == "check-inventory":
        return check_inventory(args.repair)

    if args.command == "seed":
        is_default_admin = normalize_email(args.email) == DEFAULT_ADMIN_EMAIL
        if args.full_name is None:
            if not is_default_admin:
                seed_parser.error(
                    "--name is required when --email is not the default administrator"
                )
            args.full_name = DEFAULT_ADMIN_NAME
        if args.role is None:
            args.role = DEFAULT_ADMIN_ROLE if is_default_admin else "Inventory lead"
        return seed(args.email, args.full_name, args.role, args.password_env)
    return set_password(args.email, args.password_env)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
