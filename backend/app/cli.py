"""Operator commands, run on the server: `uv run python -m app.cli --help`."""

import argparse
import getpass
import os
import sys
from collections.abc import Sequence

from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_session_factory
from app.services.accounts import (
    create_admin,
    find_resettable_account,
    reset_password_from_server,
)

PASSWORD_ENV = "CAMDOCK_ADMIN_PASSWORD"  # noqa: S105  (a variable name, not a password)


class CliError(Exception):
    """A failure whose message is safe to print."""


def _read_password() -> str:
    """From the environment (non-interactive) or a hidden prompt; never from argv."""
    from_env = os.environ.get(PASSWORD_ENV)
    if from_env:
        return from_env
    if not sys.stdin.isatty():
        raise CliError(f"No password: set {PASSWORD_ENV} or run in a terminal.")
    password = getpass.getpass("Password: ")
    if getpass.getpass("Confirm password: ") != password:
        raise CliError("Passwords do not match.")
    return password


def _create_admin(email: str) -> None:
    password = _read_password()
    with get_session_factory()() as db, db.begin():
        user = create_admin(db, email, password)
    print(f"Created admin account {user.email} (id {user.id})")


def _reset_password(email: str, assume_yes: bool) -> None:
    """Show which account is about to change, ask to confirm, then set the password."""
    with get_session_factory()() as db:
        account = find_resettable_account(db, email)
        role, shown = account.role, account.email
    if not assume_yes:
        if not sys.stdin.isatty():
            raise CliError("Not a terminal: pass --yes to confirm the reset.")
        answer = input(f"Reset the password of {role} {shown}? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            raise CliError("Cancelled. Nothing was changed.")
    password = _read_password()
    with get_session_factory()() as db, db.begin():
        user, ended = reset_password_from_server(db, email, password)
    print(f"Password reset for {user.email} (id {user.id}). {ended} session(s) ended.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    admin = commands.add_parser(
        "create-admin",
        help="create an administrator account",
        description=(
            f"Create an administrator. The password is read from {PASSWORD_ENV} "
            "or a hidden prompt, never from the command line."
        ),
    )
    admin.add_argument("--email", required=True)
    reset = commands.add_parser(
        "reset-password",
        help="set a new password for an account (for when no one can issue a reset code)",
        description=(
            "Set a new password for an active account. It ends the account's sessions. "
            f"The password is read from {PASSWORD_ENV} or a hidden prompt, never from the "
            "command line. Asks you to confirm unless --yes is given."
        ),
    )
    reset.add_argument("--email", required=True)
    reset.add_argument("--yes", action="store_true", help="skip the confirmation")
    args = parser.parse_args(argv)

    try:
        if args.command == "create-admin":
            _create_admin(args.email)
        elif args.command == "reset-password":
            _reset_password(args.email, args.yes)
    except (CliError, ValueError) as exc:  # includes policy and account errors
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except SQLAlchemyError:
        # Details stay out of the output: they can include connection information.
        print(
            "Error: the database operation failed; check DATABASE_URL.", file=sys.stderr
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
