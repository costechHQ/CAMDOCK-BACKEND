"""Operator commands, run on the server: `uv run python -m app.cli --help`."""

import argparse
import getpass
import os
import sys
from collections.abc import Sequence

from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_session_factory
from app.services.accounts import create_admin

PASSWORD_ENV = "CAMDOCK_ADMIN_PASSWORD"


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
    args = parser.parse_args(argv)

    try:
        if args.command == "create-admin":
            _create_admin(args.email)
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
