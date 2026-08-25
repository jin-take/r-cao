from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import time
from collections.abc import Callable, Iterable
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, Field

from .models import AgentRole
from .policy import (
    POLICY_VERSION,
    Phase,
    PolicyAction,
    PolicyDecision,
    evaluate_policy,
)


TOKEN_HEADER = {"alg": "HS256", "typ": "RCAO"}
TOKEN_VERSION = 1


class ActorType(str, Enum):
    OWNER = "OWNER"
    AGENT = "AGENT"
    SERVICE = "SERVICE"


class IdentityStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"


class AuthOutcome(str, Enum):
    SUCCESS = "SUCCESS"
    DENIED = "DENIED"


class IdentityError(ValueError):
    """Raised when an identity is missing or violates registry invariants."""


class AuthenticationError(ValueError):
    """Raised when a bearer token cannot establish an Actor Context."""


class ActorAuthorizationError(ValueError):
    """Raised when an authenticated actor is outside the action boundary."""


class ActorIdentity(BaseModel):
    """Canonical identity record; role is resolved from this registry, not the token."""

    actor_id: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    name: str = Field(min_length=1)
    role: AgentRole
    actor_type: ActorType
    phase: Phase
    status: IdentityStatus = IdentityStatus.ACTIVE
    task_ids: set[str] = Field(default_factory=set)
    capabilities: set[str] = Field(default_factory=set)
    identity_version: int = Field(default=1, ge=1)


class ActorContext(BaseModel):
    """Request-scoped identity used by Policy and command handlers."""

    actor_id: str
    subject: str
    name: str
    role: AgentRole
    actor_type: ActorType
    phase: Phase
    token_id: str
    issued_at: int
    expires_at: int
    task_ids: set[str] = Field(default_factory=set)
    capabilities: set[str] = Field(default_factory=set)
    identity_version: int = Field(ge=1)


class AuthAuditEvent(BaseModel):
    action: str
    outcome: AuthOutcome
    actor_id: str | None = None
    subject: str | None = None
    token_id: str | None = None
    phase: Phase | None = None
    reason: str
    occurred_at: str


class AuthAuditSink(Protocol):
    def append(self, event: AuthAuditEvent) -> None:
        """Persist an authentication event without storing the bearer token."""


@dataclass
class InMemoryAuthAuditLog:
    """Phase 1 audit adapter; #30 will replace this with transactional storage."""

    events: list[AuthAuditEvent] = field(default_factory=list)

    def append(self, event: AuthAuditEvent) -> None:
        self.events.append(event)


class IdentityRegistry:
    """Canonical in-memory identity registry used before the PostgreSQL adapter."""

    def __init__(self, identities: Iterable[ActorIdentity] = ()) -> None:
        self._by_actor_id: dict[str, ActorIdentity] = {}
        self._by_subject: dict[str, ActorIdentity] = {}
        self._revoked_tokens: set[str] = set()
        for identity in identities:
            self.register(identity)

    def register(self, identity: ActorIdentity) -> None:
        if identity.actor_id in self._by_actor_id:
            raise IdentityError(f"actor_id already registered: {identity.actor_id}")
        if identity.subject in self._by_subject:
            raise IdentityError(f"subject already registered: {identity.subject}")
        if any(
            existing.name.casefold() == identity.name.casefold()
            for existing in self._by_actor_id.values()
        ):
            raise IdentityError(f"agent name already registered: {identity.name}")
        if (
            identity.actor_type is ActorType.OWNER
            and identity.role is not AgentRole.OWNER
        ):
            raise IdentityError("Owner identity must carry the OWNER role")
        if (
            identity.actor_type is not ActorType.OWNER
            and identity.role is AgentRole.OWNER
        ):
            raise IdentityError("Only the Owner identity may carry the OWNER role")
        self._by_actor_id[identity.actor_id] = identity
        self._by_subject[identity.subject] = identity

    def get_by_subject(self, subject: str) -> ActorIdentity:
        try:
            return self._by_subject[subject]
        except KeyError as exc:
            raise IdentityError("subject is not registered") from exc

    def get_by_actor_id(self, actor_id: str) -> ActorIdentity:
        try:
            return self._by_actor_id[actor_id]
        except KeyError as exc:
            raise IdentityError("actor_id is not registered") from exc

    def suspend(self, actor_id: str) -> None:
        self.get_by_actor_id(actor_id).status = IdentityStatus.SUSPENDED

    def revoke(self, actor_id: str) -> None:
        self.get_by_actor_id(actor_id).status = IdentityStatus.REVOKED

    def revoke_token(self, token_id: str) -> None:
        self._revoked_tokens.add(token_id)

    def is_token_revoked(self, token_id: str) -> bool:
        return token_id in self._revoked_tokens


def _encode_segment(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_segment(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    try:
        return base64.b64decode(
            (value + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise AuthenticationError("malformed token encoding") from exc


def _json_segment(value: dict[str, object]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _encode_segment(encoded)


def _decode_token(token: str) -> tuple[dict[str, object], dict[str, object], bytes, bytes]:
    parts = token.strip().split(".")
    if len(parts) != 3:
        raise AuthenticationError("malformed bearer token")
    header_segment, payload_segment, signature_segment = parts
    try:
        header = json.loads(_decode_segment(header_segment))
        claims = json.loads(_decode_segment(payload_segment))
    except (AuthenticationError, json.JSONDecodeError) as exc:
        raise AuthenticationError("malformed bearer token") from exc
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise AuthenticationError("malformed bearer token")
    signature = _decode_segment(signature_segment)
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    return header, claims, signature, signing_input


def _required_string(claims: dict[str, object], name: str) -> str:
    value = claims.get(name)
    if not isinstance(value, str) or not value:
        raise AuthenticationError(f"token claim {name} is invalid")
    return value


def _required_int(claims: dict[str, object], name: str) -> int:
    value = claims.get(name)
    if type(value) is not int:
        raise AuthenticationError(f"token claim {name} is invalid")
    return value


@dataclass
class Authenticator:
    """Issue and verify Phase 1 service tokens with a canonical identity lookup."""

    secret: bytes
    phase: Phase
    registry: IdentityRegistry
    audit_log: AuthAuditSink = field(default_factory=InMemoryAuthAuditLog)
    issuer: str = "rcao-control-plane"
    clock: Callable[[], float] = time.time
    clock_skew_seconds: int = 0

    def __post_init__(self) -> None:
        if len(self.secret) < 32:
            raise ValueError("authentication secret must be at least 32 bytes")
        if self.clock_skew_seconds < 0:
            raise ValueError("clock_skew_seconds must be non-negative")

    def _now(self) -> int:
        return int(self.clock())

    def issue_token(self, subject: str, *, ttl_seconds: int = 900) -> str:
        if ttl_seconds < 1 or ttl_seconds > 86_400:
            raise ValueError("ttl_seconds must be between 1 and 86400")
        identity = self.registry.get_by_subject(subject)
        self._ensure_active(identity)
        if identity.phase is not self.phase:
            raise IdentityError("identity belongs to a different execution phase")

        issued_at = self._now()
        claims: dict[str, object] = {
            "ver": TOKEN_VERSION,
            "iss": self.issuer,
            "sub": identity.subject,
            "tid": secrets.token_urlsafe(16),
            "iat": issued_at,
            "exp": issued_at + ttl_seconds,
            "phase": self.phase.value,
            "iv": identity.identity_version,
        }
        header_segment = _json_segment(TOKEN_HEADER)
        payload_segment = _json_segment(claims)
        signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
        signature = hmac.new(
            self.secret,
            signing_input,
            hashlib.sha256,
        ).digest()
        return f"{header_segment}.{payload_segment}.{_encode_segment(signature)}"

    def authenticate(self, token: str) -> ActorContext:
        subject: str | None = None
        token_id: str | None = None
        phase: Phase | None = None
        try:
            header, claims, signature, signing_input = _decode_token(token)
            if header != TOKEN_HEADER:
                raise AuthenticationError("unsupported token header")

            expected_signature = hmac.new(
                self.secret,
                signing_input,
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(signature, expected_signature):
                raise AuthenticationError("invalid token signature")

            if claims.get("ver") != TOKEN_VERSION:
                raise AuthenticationError("unsupported token version")
            if claims.get("iss") != self.issuer:
                raise AuthenticationError("invalid token issuer")

            subject = _required_string(claims, "sub")
            token_id = _required_string(claims, "tid")
            issued_at = _required_int(claims, "iat")
            expires_at = _required_int(claims, "exp")
            phase_value = _required_string(claims, "phase")
            identity_version = _required_int(claims, "iv")
            try:
                phase = Phase(phase_value)
            except ValueError as exc:
                raise AuthenticationError("invalid token phase") from exc

            now = self._now()
            if phase is not self.phase:
                raise AuthenticationError("token belongs to a different execution phase")
            if expires_at <= issued_at:
                raise AuthenticationError("token expiry is invalid")
            if issued_at > now + self.clock_skew_seconds:
                raise AuthenticationError("token is not active yet")
            if expires_at <= now - self.clock_skew_seconds:
                raise AuthenticationError("token has expired")
            if self.registry.is_token_revoked(token_id):
                raise AuthenticationError("token has been revoked")

            identity = self.registry.get_by_subject(subject)
            self._ensure_active(identity)
            if identity.phase is not self.phase:
                raise AuthenticationError(
                    "identity belongs to a different execution phase"
                )
            if identity.identity_version != identity_version:
                raise AuthenticationError("identity version is no longer current")

            context = ActorContext(
                actor_id=identity.actor_id,
                subject=identity.subject,
                name=identity.name,
                role=identity.role,
                actor_type=identity.actor_type,
                phase=identity.phase,
                token_id=token_id,
                issued_at=issued_at,
                expires_at=expires_at,
                task_ids=set(identity.task_ids),
                capabilities=set(identity.capabilities),
                identity_version=identity.identity_version,
            )
            self._record(
                action="AUTHENTICATE",
                outcome=AuthOutcome.SUCCESS,
                actor_id=identity.actor_id,
                subject=identity.subject,
                token_id=token_id,
                phase=identity.phase,
                reason="authenticated",
            )
            return context
        except (AuthenticationError, IdentityError) as exc:
            self._record(
                action="AUTHENTICATE",
                outcome=AuthOutcome.DENIED,
                subject=subject,
                token_id=token_id,
                phase=phase,
                reason=str(exc),
            )
            raise AuthenticationError("invalid actor authentication") from exc

    def _ensure_active(self, identity: ActorIdentity) -> None:
        if identity.status is not IdentityStatus.ACTIVE:
            raise AuthenticationError("identity is not active")

    def _record(
        self,
        *,
        action: str,
        outcome: AuthOutcome,
        reason: str,
        actor_id: str | None = None,
        subject: str | None = None,
        token_id: str | None = None,
        phase: Phase | None = None,
    ) -> None:
        self.audit_log.append(
            AuthAuditEvent(
                action=action,
                outcome=outcome,
                actor_id=actor_id,
                subject=subject,
                token_id=token_id,
                phase=phase,
                reason=reason,
                occurred_at=datetime.now(timezone.utc).isoformat(),
            )
        )


def evaluate_actor_policy(
    context: ActorContext,
    action: PolicyAction,
    *,
    task_id: str | None = None,
) -> tuple[PolicyDecision, str | None]:
    if context.actor_type is not ActorType.OWNER:
        if task_id is None:
            return (
                PolicyDecision.DENY,
                "non-owner actors require a task_id",
            )
        if task_id not in context.task_ids:
            return (
                PolicyDecision.DENY,
                "actor is not a member of the requested task",
            )

    decision = evaluate_policy(context.role, action, phase=context.phase)
    if decision is PolicyDecision.ALLOW:
        return decision, None
    if decision is PolicyDecision.REQUIRE_OWNER_APPROVAL:
        return decision, "Owner approval is required"
    return decision, "action is denied by the constitutional Policy"


def authorize_actor_action(
    context: ActorContext,
    action: PolicyAction,
    *,
    task_id: str | None = None,
) -> PolicyDecision:
    decision, reason = evaluate_actor_policy(context, action, task_id=task_id)
    if decision is not PolicyDecision.ALLOW:
        raise ActorAuthorizationError(reason or "actor action is not authorized")
    return decision


def assert_owner_actor(context: ActorContext) -> None:
    if (
        context.actor_type is not ActorType.OWNER
        or context.role is not AgentRole.OWNER
    ):
        raise ActorAuthorizationError("Owner authority is required")


def require_actor(
    authorization: str | None = Header(default=None),
) -> ActorContext:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Bearer authentication is required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Bearer authentication is required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return get_runtime_authenticator().authenticate(token)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=401,
            detail="Invalid actor authentication",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def require_owner_actor(
    actor: ActorContext = Depends(require_actor),
) -> ActorContext:
    try:
        assert_owner_actor(actor)
    except ActorAuthorizationError as exc:
        raise HTTPException(
            status_code=403,
            detail="Owner authority is required",
        ) from exc
    return actor


def build_runtime_authenticator() -> Authenticator:
    secret = os.getenv("RCAO_AUTH_SECRET", "")
    if len(secret.encode("utf-8")) < 32:
        raise AuthenticationError(
            "RCAO_AUTH_SECRET must contain at least 32 bytes"
        )
    try:
        phase = Phase(
            os.getenv("RCAO_PHASE", Phase.PHASE_1_OFFCHAIN.value)
        )
    except ValueError as exc:
        raise AuthenticationError("RCAO_PHASE is not a supported Phase") from exc

    owner_id = os.getenv("RCAO_OWNER_ID", "owner-local")
    owner_subject = os.getenv("RCAO_OWNER_SUBJECT", owner_id)
    owner_name = os.getenv("RCAO_OWNER_NAME", "Owner")
    registry = IdentityRegistry(
        [
            ActorIdentity(
                actor_id=owner_id,
                subject=owner_subject,
                name=owner_name,
                role=AgentRole.OWNER,
                actor_type=ActorType.OWNER,
                phase=phase,
            )
        ]
    )
    return Authenticator(
        secret=secret.encode("utf-8"),
        phase=phase,
        registry=registry,
        issuer=os.getenv("RCAO_AUTH_ISSUER", "rcao-control-plane"),
    )


_runtime_authenticator: Authenticator | None = None


def configure_runtime_authenticator(authenticator: Authenticator | None) -> None:
    global _runtime_authenticator
    _runtime_authenticator = authenticator


def get_runtime_authenticator() -> Authenticator:
    global _runtime_authenticator
    if _runtime_authenticator is None:
        _runtime_authenticator = build_runtime_authenticator()
    return _runtime_authenticator


class PolicyCheckRequest(BaseModel):
    action: PolicyAction
    task_id: str | None = Field(default=None, min_length=1)


class PolicyCheckResponse(BaseModel):
    actor_id: str
    action: PolicyAction
    task_id: str | None = None
    decision: PolicyDecision
    policy_version: str = POLICY_VERSION
    reason: str | None = None
