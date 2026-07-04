"""Platform users CLI: create-admin | list | reset-password."""

from __future__ import annotations

import argparse
import getpass

from waloader.repositories import users as users_repo
from waloader.services import security, users_service
from waloader.tools._common import bootstrap, fail


def _prompt_password(confirm: bool = True) -> str:
    password = getpass.getpass("Password: ")
    if confirm and getpass.getpass("Repeat password: ") != password:
        raise fail("Passwords do not match")
    return password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="waloader.tools.users", description="Manage WALoader platform users"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-admin", help="create an administrator account")
    create.add_argument("username")
    create.add_argument("--email", default="")
    create.add_argument(
        "--password",
        help="password (omit to be prompted securely — preferred)",
    )

    sub.add_parser("list")

    reset = sub.add_parser("reset-password")
    reset.add_argument("username")
    reset.add_argument("--password", help="omit to be prompted securely")

    args = parser.parse_args(argv)
    config, conn = bootstrap()
    try:
        if args.command == "create-admin":
            password = args.password or _prompt_password()
            try:
                user = users_service.create_user(
                    conn, username=args.username, email=args.email,
                    password=password, is_admin=True, actor="cli",
                )
            except (users_service.UserValidationError, security.WeakPasswordError) as exc:
                raise fail(str(exc)) from exc
            print(f"admin '{user.username}' created")
        elif args.command == "list":
            for user in users_repo.list_all(conn):
                role = "admin" if user.is_admin else "user"
                state = "active" if user.is_active else "inactive"
                print(f"{user.username:24} {role:6} {state:9} {user.email}")
        elif args.command == "reset-password":
            user = users_repo.get_by_username(conn, args.username)
            if user is None:
                raise fail(f"No user '{args.username}'")
            password = args.password or _prompt_password()
            try:
                users_service.admin_reset_password(conn, user.id, password, actor="cli")
            except security.WeakPasswordError as exc:
                raise fail(str(exc)) from exc
            print(f"password reset for '{user.username}'")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
