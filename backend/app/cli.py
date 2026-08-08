"""Administrative commands.

    python -m app.cli seed                                    # default administrator
    python -m app.cli seed --email you@deodap.in --name "Your Name"
    python -m app.cli set-password --email you@deodap.in
    python -m app.cli backup                                   # snapshot + prune
    python -m app.cli check-inventory                          # report inconsistencies
    python -m app.cli check-inventory --repair                 # and fix them

There is no self-registration: this is an internal tool where accounts are
issued.

**No account is ever created with a password this file knows.** There used to
be a built-in one for the default administrator, documented in the README and
therefore public. It was a development convenience that shipped: the production
deployment ran on it, which made the admin credential of a live internet-facing
service a constant in a public repository. A password nobody typed is a
password nobody owns, so there is no longer a way to get an account without
supplying one.

Passwords are read from the environment or prompted for interactively, never
passed as arguments, so they never land in shell history or the process list:

* ``--password-env NAME`` reads that variable. Named explicitly, so an unset
  one is an error rather than a cue to prompt — a scripted caller wants to
  fail, not block.
* Otherwise ``STOCKSYNC_ADMIN_PASSWORD``, which is what makes ``reset-db``
  work unattended in development.
* Otherwise an interactive prompt, twice, with no echo.

None of these paths writes the value anywhere it can be read back.
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

from app.config import get_settings
from app.core.security import hash_password
from app.db.session import get_session_factory
from app.models import InventoryItem, User, UserPreferences, Workspace, normalize_email
from app.services import backup as backup_service

DEFAULT_WORKSPACE_NAME = "Deodap Retail"
DEFAULT_WORKSPACE_SLUG = "deodap"
MIN_PASSWORD_LENGTH = 12

# The administrator issued to every new database. The address is a convention,
# not a credential — there is no password here, by design.
DEFAULT_ADMIN_EMAIL = "admin@deodap.in"
DEFAULT_ADMIN_NAME = "Administrator"
DEFAULT_ADMIN_ROLE = "Admin"

#: Consulted when no --password-env is named. Having one conventional variable
#: is what lets `reset-db` rebuild and seed unattended without this file
#: carrying a password of its own.
#:
#: The suppression is for the *name* of an environment variable, not a value —
#: S105 matches any constant whose name contains "PASSWORD". An identical
#: suppression used to sit on a real password two lines from here, which is how
#: that one survived review, so: this string is a lookup key, and
#: `test_cli_credentials.py` fails the build if a password constant reappears
#: under any name.
ADMIN_PASSWORD_ENV = "STOCKSYNC_ADMIN_PASSWORD"  # noqa: S105


def _read_password(password_env: str | None = None, confirm: bool = True) -> str:
    """Read a password without it ever appearing in argv.

    Order: the named environment variable, then ``STOCKSYNC_ADMIN_PASSWORD``,
    then an interactive prompt. There is deliberately no "detect a
    non-interactive terminal and generate one" path — ``sys.stdin.isatty()`` is
    not reliable across Windows terminals (it reports True under redirection in
    Git Bash), so that branch would hang precisely where automation needs it
    not to. The EOF below is the honest version of that check: it fires only
    once a prompt has actually failed to find a terminal.
    """
    name = password_env or ADMIN_PASSWORD_ENV
    value = os.environ.get(name)
    if value:
        if len(value) < MIN_PASSWORD_LENGTH:
            raise SystemExit(f"{name} must be at least {MIN_PASSWORD_LENGTH} characters.")
        return value

    if password_env:
        # Named explicitly, so an empty one is a mistake in the caller's setup.
        # Falling through to a prompt would hang a deployment script instead.
        raise SystemExit(f"Environment variable {password_env} is unset or empty.")

    try:
        while True:
            password = getpass.getpass("Password: ")
            if len(password) < MIN_PASSWORD_LENGTH:
                print(f"Too short — use at least {MIN_PASSWORD_LENGTH} characters.")
                continue
            if confirm and password != getpass.getpass("Confirm password: "):
                print("Those didn't match. Try again.")
                continue
            return password
    except (EOFError, KeyboardInterrupt):
        raise SystemExit(
            "No password supplied and no terminal to ask for one.\n"
            f"Set {ADMIN_PASSWORD_ENV}, or pass --password-env NAME naming a variable "
            "that holds it."
        ) from None


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
    # Only affects whether re-seeding is treated as success and whether --name
    # may be omitted. It no longer selects a password: every account, this one
    # included, is created with one the operator supplied.
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

        password = _read_password(password_env)
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

        # The password is deliberately absent from this line. It was printed
        # here once, which put it in shell history, CI logs and journald — all
        # places a credential outlives the terminal that created it.
        print(f"Created {user.full_name} <{user.email}> in workspace {workspace.name!r}.")
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


def backup() -> int:
    """Take a consistent database snapshot and prune old ones.

    Meant for cron or a systemd timer — see ``deploy/README.md``. Exits non-zero
    when the snapshot fails, so a scheduler that checks exit codes reports it
    rather than logging success into the void.
    """
    settings = get_settings()
    try:
        destination = backup_service.run(settings)
    except backup_service.BackupError as caught:
        print(str(caught), file=sys.stderr)
        return 1

    kept = sorted(settings.backup_dir.glob("stocksync-*.db"))
    print(f"Wrote {destination} ({destination.stat().st_size:,} bytes).")
    print(f"Keeping {len(kept)} of at most {settings.backup_keep} snapshots.")
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

    sub.add_parser("backup", help="write a database snapshot and prune old ones")

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

    if args.command == "backup":
        return backup()

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
