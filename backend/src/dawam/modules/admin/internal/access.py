"""Who may use the admin console: one guard per installation-wide policy action."""

from __future__ import annotations

from collections.abc import Callable

from dawam.modules.auth import CurrentUser, User
from dawam.modules.workspaces import INSTALLATION, Action, can
from dawam.platform.errors import ApiError


def allowed_to(action: Action) -> Callable[[User], User]:
    """A route dependency giving the signed-in user if the central policy lets them
    perform ``action`` on the installation (admins); anyone else gets ``403 forbidden``
    (anonymous ``401 unauthenticated``)."""

    def guard(user: CurrentUser) -> User:
        if not can(user, action, INSTALLATION):
            raise ApiError(403, "forbidden", "Only admins can do this.")
        return user

    return guard
