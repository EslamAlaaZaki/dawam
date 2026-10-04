"""``/api/v1/auth/login``, ``/api/v1/auth/logout`` and ``/api/v1/me``.

The session token travels only in the ``dawam_session`` cookie: ``HttpOnly``,
``SameSite=Lax``, ``Secure`` when the request came over TLS, and kept by the browser
for the absolute session timeout. The CSRF token is checked for every state-changing
request by the API router (``dawam.platform.csrf``), sign-in included.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from dawam.platform.errors import ApiError

from .credentials import MAX_EMAIL_LENGTH, MAX_PASSWORD_LENGTH
from .service import AuthService, SystemRole, User

SESSION_COOKIE = "dawam_session"

router = APIRouter(tags=["auth"])


def auth_service(request: Request) -> AuthService:
    state = request.app.state
    return AuthService(state.engine, state.settings, clock=state.services.clock)


AuthServiceDep = Annotated[AuthService, Depends(auth_service)]


def current_user(request: Request, auth: AuthServiceDep) -> User:
    """The signed-in user; anonymous requests (or ended sessions) get ``401 unauthenticated``."""
    token = request.cookies.get(SESSION_COOKIE)
    user = auth.user_for_session(token) if token else None
    if user is None:
        raise ApiError(401, "unauthenticated", "Sign in to continue.")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
"""Declare a parameter of this type to make a route require a signed-in user."""


class SignInRequest(BaseModel):
    # Bounded, so an oversized password is rejected (422) before argon2 sees it.
    email: str = Field(max_length=MAX_EMAIL_LENGTH)
    password: str = Field(max_length=MAX_PASSWORD_LENGTH)


class Me(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    system_role: SystemRole


def _is_tls(request: Request) -> bool:
    return request.url.scheme == "https"


@router.post("/auth/login", operation_id="signIn")
def sign_in(body: SignInRequest, request: Request, response: Response, auth: AuthServiceDep) -> Me:
    """Sign in with email and password; sets the session cookie."""
    signed_in = auth.sign_in(
        body.email,
        body.password,
        replacing=request.cookies.get(SESSION_COOKIE),
        # uvicorn sets the client from X-Forwarded-For only for DAWAM_FORWARDED_ALLOW_IPS.
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    response.set_cookie(
        SESSION_COOKIE,
        signed_in.token,
        max_age=int(auth.absolute_timeout.total_seconds()),
        path="/",
        secure=_is_tls(request),
        httponly=True,
        samesite="lax",
    )
    return Me.model_validate(signed_in.user)


@router.post("/auth/logout", operation_id="signOut", status_code=204, response_class=Response)
def sign_out(request: Request, auth: AuthServiceDep) -> Response:
    """End this browser's session on the server and clear its cookie."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        auth.sign_out(token)
    response = Response(status_code=204)
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=_is_tls(request), httponly=True, samesite="lax"
    )
    return response


@router.get("/me", operation_id="getMe")
def get_me(user: CurrentUser) -> Me:
    """The signed-in user."""
    return Me.model_validate(user)
