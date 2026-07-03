"""Authorization checks: admin-only, app-owner, and app-user boundaries."""

from __future__ import annotations

from waloader.models import App, User


class NotAuthorizedError(PermissionError):
    pass


def is_admin(user: User) -> bool:
    return bool(user.is_admin) and bool(user.is_active)


def require_admin(user: User) -> None:
    if not is_admin(user):
        raise NotAuthorizedError("This action requires a WALoader administrator")


def can_manage_app(user: User, app: App) -> bool:
    """Admins manage everything; owners manage their own apps."""
    if not user.is_active:
        return False
    return is_admin(user) or app.owner_id == user.id


def require_app_manager(user: User, app: App) -> None:
    if not can_manage_app(user, app):
        raise NotAuthorizedError(f"You do not manage the app '{app.name}'")
