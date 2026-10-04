"""Sign-up (``/api/v1/auth/register``, ``/registration``), sign-in and sessions
(``/api/v1/auth/login``, ``/logout``, ``/logout-all``), the password change
(``/api/v1/auth/password/change``) and the user's profile (``/api/v1/me``).

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

from .internal.credentials import MAX_EMAIL_LENGTH, MAX_PASSWORD_LENGTH
from .service import AuthService, RegistrationPolicy, SignedIn, SystemRole, User

SESSION_COOKIE = "dawam_session"

# Bounds the input only; the display-name rules (trimmed, at most 200) are the service's.
_MAX_DISPLAY_NAME_INPUT = 1000

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


def registration_policy(request: Request) -> RegistrationPolicy | None:
    """Set by the composition root (``app.state.registration_policy``); without one,
    self-registration is closed."""
    return getattr(request.app.state, "registration_policy", None)


RegistrationPolicyDep = Annotated[RegistrationPolicy | None, Depends(registration_policy)]


class RegisterRequest(BaseModel):
    email: str = Field(max_length=MAX_EMAIL_LENGTH)
    password: str = Field(max_length=MAX_PASSWORD_LENGTH)
    display_name: str = Field(max_length=_MAX_DISPLAY_NAME_INPUT)


class RegistrationStatus(BaseModel):
    open: bool = Field(description="Whether visitors may sign up on their own.")


class UpdateMeRequest(BaseModel):
    display_name: str = Field(max_length=_MAX_DISPLAY_NAME_INPUT)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(max_length=MAX_PASSWORD_LENGTH)


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
        **_client_of(request),
    )
    return _signed_in(signed_in, request, response, auth)


@router.get("/auth/registration", operation_id="getRegistration")
def get_registration(policy: RegistrationPolicyDep) -> RegistrationStatus:
    """Whether self-registration is open (anyone may ask; the sign-up link shows only
    then)."""
    return RegistrationStatus(open=policy is not None and policy.registration_open())


@router.post("/auth/register", operation_id="register", status_code=201)
def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    auth: AuthServiceDep,
    policy: RegistrationPolicyDep,
) -> Me:
    """Sign up with email, display name and password while self-registration is open
    (and the email's domain is allowed); signs the new user in."""
    if policy is None:
        raise ApiError(403, "registration_closed", "Self-registration is turned off.")
    signed_in = auth.register(
        email=body.email,
        password=body.password,
        display_name=body.display_name,
        policy=policy,
        replacing=request.cookies.get(SESSION_COOKIE),
        **_client_of(request),
    )
    return _signed_in(signed_in, request, response, auth)


@router.post("/auth/logout", operation_id="signOut", status_code=204, response_class=Response)
def sign_out(request: Request, auth: AuthServiceDep) -> Response:
    """End this browser's session on the server and clear its cookie."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        auth.sign_out(token)
    return _signed_out(request)


@router.post(
    "/auth/logout-all", operation_id="signOutEverywhere", status_code=204, response_class=Response
)
def sign_out_everywhere(user: CurrentUser, request: Request, auth: AuthServiceDep) -> Response:
    """End every session of the signed-in user (this one too) and clear this cookie."""
    auth.sign_out_everywhere(user.id)
    return _signed_out(request)


@router.post(
    "/auth/password/change",
    operation_id="changePassword",
    status_code=204,
    response_class=Response,
)
def change_password(
    body: ChangePasswordRequest, user: CurrentUser, request: Request, auth: AuthServiceDep
) -> Response:
    """Change the signed-in user's password, given the current one. Every other session
    of the user ends; this one stays signed in."""
    auth.change_password(
        user.id,
        body.current_password,
        body.new_password,
        keep_session=request.cookies.get(SESSION_COOKIE),
    )
    return Response(status_code=204)


@router.get("/me", operation_id="getMe")
def get_me(user: CurrentUser) -> Me:
    """The signed-in user."""
    return Me.model_validate(user)


@router.patch("/me", operation_id="updateMe")
def update_me(body: UpdateMeRequest, user: CurrentUser, auth: AuthServiceDep) -> Me:
    """Change the signed-in user's display name."""
    return Me.model_validate(auth.update_display_name(user.id, body.display_name))


def _client_of(request: Request) -> dict[str, str | None]:
    return {
        # uvicorn sets the client from X-Forwarded-For only for DAWAM_FORWARDED_ALLOW_IPS.
        "ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


def _signed_in(signed_in: SignedIn, request: Request, response: Response, auth: AuthService) -> Me:
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


def _signed_out(request: Request) -> Response:
    response = Response(status_code=204)
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=_is_tls(request), httponly=True, samesite="lax"
    )
    return response
