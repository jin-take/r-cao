"""Owner-controlled Agent Payment Profiles for the MPP boundary.

Payment Profiles describe the *limits* under which an Agent may request an
external Service Payment.  They are policy input, not signing authority:
there is deliberately no private key, seed phrase, signed transaction, or
network client in this module.

The current profile row is versioned with optimistic concurrency.  Every
mutation also appends a profile snapshot and a normalised Audit event.  A
Payment may retain ``profile_id`` and ``profile_version`` as a snapshot
reference; changing a profile never mutates an existing Payment.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Mapping

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from .audit import AuditEvent, AuditWriter
from .policy import MPP_POLICY_VERSION, PolicyDecision


MAX_SIGNED_BIGINT = (1 << 63) - 1
SERVICE_RECIPIENT_KIND = "SERVICE"
FORBIDDEN_PROFILE_TOKENS = frozenset(
    {"SOL", "VIRTUAL", "VIRTUAL_REWARD", "REWARD", "TREASURY"}
)


class PaymentProfileError(ValueError):
    """Base error for invalid or unsafe Payment Profile operations."""


class PaymentProfileAuthorizationError(PaymentProfileError):
    """The caller is not the Owner allowed to mutate a profile."""


class PaymentProfileNotFoundError(PaymentProfileError):
    """The requested profile is not registered."""


class PaymentProfileConcurrencyError(PaymentProfileError):
    """A profile changed after the caller read its version."""


class PaymentProfileApprovalRequiredError(PaymentProfileError):
    """A security-sensitive expansion lacks an explicit Owner approval."""


class PaymentProfileConflictError(PaymentProfileError):
    """The profile identity conflicts with an existing active profile."""


class PaymentProfileNetwork(str, Enum):
    LOCAL = "LOCAL"
    SOLANA_DEVNET = "SOLANA_DEVNET"


class PaymentProfileStatus(str, Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    SUSPENDED = "SUSPENDED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class PaymentApprovalMode(str, Enum):
    AUTO_ALLOW = "AUTO_ALLOW"
    OWNER_APPROVAL = "OWNER_APPROVAL"
    DENY = "DENY"


class PaymentProfileRiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class PaymentProfileRotationState(str, Enum):
    CURRENT = "CURRENT"
    PENDING = "PENDING"
    RETIRED = "RETIRED"
    REVOKED = "REVOKED"


# Short aliases keep the contract easy to discover for callers and tests.
ApprovalMode = PaymentApprovalMode
ProfileNetwork = PaymentProfileNetwork
ProfileStatus = PaymentProfileStatus
RiskLevel = PaymentProfileRiskLevel
RotationState = PaymentProfileRotationState


def _normalise_utc(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PaymentProfileError("profile timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _string_tuple(value: Any, *, field_name: str, allow_empty: bool = False) -> tuple[str, ...]:
    if value is None:
        values: tuple[Any, ...] = ()
    elif isinstance(value, str):
        values = (value,)
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = tuple(value)
    else:
        raise ValueError(f"{field_name} must be a list of strings")
    if not allow_empty and not values:
        raise ValueError(f"{field_name} must contain at least one value")
    if not all(isinstance(item, str) for item in values):
        raise ValueError(f"{field_name} must contain only strings")
    result = tuple(item.strip() for item in values)
    if any(not item for item in result):
        raise ValueError(f"{field_name} cannot contain empty values")
    if any(any(ord(character) < 32 for character in item) for item in result):
        raise ValueError(f"{field_name} cannot contain control characters")
    if len(set(result)) != len(result):
        raise ValueError(f"{field_name} cannot contain duplicates")
    return result


class PaymentProfileSpec(BaseModel):
    """Mutable, secret-free Payment Profile fields shared by commands/rows."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
    )

    agent_id: str = Field(min_length=1, max_length=200)
    wallet_id: str | None = Field(default=None, min_length=1, max_length=300)
    public_key: str | None = Field(default=None, min_length=1, max_length=300)
    network: PaymentProfileNetwork
    cluster: str | None = Field(default=None, min_length=1, max_length=80)
    service_id: str = Field(min_length=1, max_length=300)
    recipient: str = Field(min_length=1, max_length=300)
    recipient_kind: Literal["SERVICE"] = SERVICE_RECIPIENT_KIND
    token_allowlist: tuple[str, ...] = Field(min_length=1)
    mint_allowlist: tuple[str, ...] = Field(default_factory=tuple)
    service_allowlist: tuple[str, ...] = Field(min_length=1)
    recipient_allowlist: tuple[str, ...] = Field(min_length=1)
    program_allowlist: tuple[str, ...] = Field(default_factory=tuple)
    purpose_allowlist: tuple[str, ...] = ("SERVICE_PAYMENT",)
    risk_level: PaymentProfileRiskLevel = PaymentProfileRiskLevel.LOW
    approval_mode: PaymentApprovalMode = PaymentApprovalMode.OWNER_APPROVAL
    per_payment_limit_units: StrictInt = Field(
        validation_alias=AliasChoices(
            "per_payment_limit_units",
            "max_amount_units",
            "per_payment_limit_lamports",
        ),
        serialization_alias="per_payment_limit_units",
        gt=0,
        le=MAX_SIGNED_BIGINT,
    )
    per_task_limit_units: StrictInt = Field(
        validation_alias=AliasChoices(
            "per_task_limit_units",
            "task_limit_units",
            "max_task_amount_units",
        ),
        serialization_alias="per_task_limit_units",
        gt=0,
        le=MAX_SIGNED_BIGINT,
    )
    daily_limit_units: StrictInt = Field(
        validation_alias=AliasChoices(
            "daily_limit_units",
            "max_daily_amount_units",
            "daily_limit_lamports",
        ),
        serialization_alias="daily_limit_units",
        gt=0,
        le=MAX_SIGNED_BIGINT,
    )
    auto_approval_limit_units: StrictInt = Field(
        default=0,
        validation_alias=AliasChoices(
            "auto_approval_limit_units",
            "automatic_approval_limit_units",
        ),
        serialization_alias="auto_approval_limit_units",
        ge=0,
        le=MAX_SIGNED_BIGINT,
    )
    max_expiry_seconds: StrictInt = Field(gt=0, le=86_400)
    expires_at: datetime
    status: PaymentProfileStatus = PaymentProfileStatus.ACTIVE
    rotation_state: PaymentProfileRotationState = PaymentProfileRotationState.CURRENT

    @field_validator(
        "agent_id",
        "wallet_id",
        "public_key",
        "cluster",
        "service_id",
        "recipient",
    )
    @classmethod
    def reject_control_whitespace(cls, value: str | None) -> str | None:
        if value is not None and any(ord(character) < 32 for character in value):
            raise ValueError("profile identifiers cannot contain control characters")
        return value

    @field_validator(
        "token_allowlist",
        "mint_allowlist",
        "service_allowlist",
        "recipient_allowlist",
        "program_allowlist",
        "purpose_allowlist",
        mode="before",
    )
    @classmethod
    def normalise_allowlists(cls, value: Any, info: Any) -> tuple[str, ...]:
        return _string_tuple(
            value,
            field_name=info.field_name,
            allow_empty=info.field_name in {"mint_allowlist", "program_allowlist"},
        )

    @field_validator("expires_at")
    @classmethod
    def require_expiry_timezone(cls, value: datetime) -> datetime:
        return _normalise_utc(value)

    @model_validator(mode="after")
    def validate_profile_contract(self) -> "PaymentProfileSpec":
        expected_cluster = (
            "LOCAL" if self.network is PaymentProfileNetwork.LOCAL else "DEVNET"
        )
        if self.cluster is None:
            self.cluster = expected_cluster
        elif self.cluster.upper() != expected_cluster:
            raise ValueError("cluster must match the selected Payment Profile network")

        if self.recipient_kind != SERVICE_RECIPIENT_KIND:
            raise ValueError("Payment Profiles only permit SERVICE recipients")
        if self.service_id not in self.service_allowlist:
            raise ValueError("service_id must be included in service_allowlist")
        if self.recipient not in self.recipient_allowlist:
            raise ValueError("recipient must be included in recipient_allowlist")
        if self.purpose_allowlist != ("SERVICE_PAYMENT",):
            raise ValueError("Payment Profiles only permit SERVICE_PAYMENT")

        expected_prefix = (
            "LOCAL_TEST_"
            if self.network is PaymentProfileNetwork.LOCAL
            else "SPL_TEST_"
        )
        for token in self.token_allowlist:
            upper = token.upper()
            if upper in FORBIDDEN_PROFILE_TOKENS:
                raise ValueError("Reward, Treasury, and SOL assets are not MPP tokens")
            if not upper.startswith(expected_prefix):
                raise ValueError(
                    f"token allowlist values must use the {expected_prefix} fixture prefix"
                )
        for mint in self.mint_allowlist:
            if not mint.upper().startswith(expected_prefix):
                raise ValueError(
                    f"mint allowlist values must use the {expected_prefix} fixture prefix"
                )

        for value_name, value in (
            ("wallet_id", self.wallet_id),
            ("public_key", self.public_key),
        ):
            if value is not None and any(
                marker in value.casefold()
                for marker in ("private", "secret", "seed", "mnemonic", "signature")
            ):
                raise ValueError(f"{value_name} cannot contain secret material")

        lowered_recipient = self.recipient.casefold()
        if lowered_recipient == self.agent_id.casefold() or lowered_recipient.startswith(
            ("agent:", "agent-", "owner:", "owner-", "treasury:", "treasury-", "ledger:", "ledger-")
        ):
            raise ValueError("Payment Profile recipient must be an external Service")
        if self.per_payment_limit_units > self.per_task_limit_units:
            raise ValueError("per-payment limit cannot exceed per-Task limit")
        if self.per_task_limit_units > self.daily_limit_units:
            raise ValueError("per-Task limit cannot exceed daily limit")
        if self.auto_approval_limit_units > self.per_payment_limit_units:
            raise ValueError("automatic approval limit cannot exceed per-payment limit")
        return self

    def public_state(self) -> dict[str, Any]:
        """Return a serialisable state with no credential-bearing fields."""

        return {
            "profile_id": getattr(self, "profile_id", None),
            "agent_id": self.agent_id,
            "version": getattr(self, "version", 1),
            "wallet_id": self.wallet_id,
            "public_key": self.public_key,
            "network": self.network.value,
            "cluster": self.cluster,
            "service_id": self.service_id,
            "recipient": self.recipient,
            "recipient_kind": self.recipient_kind,
            "token_allowlist": list(self.token_allowlist),
            "mint_allowlist": list(self.mint_allowlist),
            "service_allowlist": list(self.service_allowlist),
            "recipient_allowlist": list(self.recipient_allowlist),
            "program_allowlist": list(self.program_allowlist),
            "purpose_allowlist": list(self.purpose_allowlist),
            "risk_level": self.risk_level.value,
            "approval_mode": self.approval_mode.value,
            "per_payment_limit_units": self.per_payment_limit_units,
            "per_task_limit_units": self.per_task_limit_units,
            "daily_limit_units": self.daily_limit_units,
            "auto_approval_limit_units": self.auto_approval_limit_units,
            "max_expiry_seconds": self.max_expiry_seconds,
            "expires_at": self.expires_at.isoformat(),
            "status": self.status.value,
            "rotation_state": self.rotation_state.value,
            "created_by": getattr(self, "created_by", None),
            "owner_approval_id": getattr(self, "owner_approval_id", None),
        }


class AgentPaymentProfile(PaymentProfileSpec):
    """Persisted Payment Profile, including immutable identity/version data."""

    profile_id: str = Field(
        min_length=1,
        max_length=200,
        validation_alias=AliasChoices("profile_id", "id"),
        serialization_alias="profile_id",
    )
    version: StrictInt = Field(default=1, ge=1)
    created_by: str = Field(default="owner-local", min_length=1, max_length=200)
    owner_approval_id: str | None = Field(default=None, max_length=200)
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None

    @field_validator("created_at", "updated_at")
    @classmethod
    def normalise_optional_timestamp(cls, value: datetime | str | None) -> datetime | str | None:
        return _normalise_utc(value) if value is not None else None


class PaymentProfileCreateCommand(PaymentProfileSpec):
    """FastAPI/request contract; actor and audit fields come from auth."""

    profile_id: str = Field(min_length=1, max_length=200)

    def to_profile(self, *, actor_id: str) -> AgentPaymentProfile:
        return AgentPaymentProfile(
            profile_id=self.profile_id,
            version=1,
            created_by=actor_id,
            **self.model_dump(exclude={"profile_id"}),
        )


class PaymentProfileUpdateCommand(PaymentProfileSpec):
    """Full replacement command guarded by an expected current version."""

    profile_id: str = Field(min_length=1, max_length=200)
    expected_version: StrictInt = Field(ge=1)
    owner_approval_id: str | None = Field(default=None, max_length=200)

    def to_profile(self, *, actor_id: str) -> AgentPaymentProfile:
        return AgentPaymentProfile(
            profile_id=self.profile_id,
            version=self.expected_version + 1,
            created_by=actor_id,
            owner_approval_id=self.owner_approval_id,
            **self.model_dump(
                exclude={"profile_id", "expected_version", "owner_approval_id"}
            ),
        )


class PaymentProfileStatusCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: PaymentProfileStatus
    expected_version: StrictInt = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1_000)


class PaymentProfileRotationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_version: StrictInt = Field(ge=1)
    public_key: str = Field(min_length=1, max_length=300)
    wallet_id: str | None = Field(default=None, min_length=1, max_length=300)
    reason: str = Field(
        default="Owner rotated the Payment Profile public identity",
        min_length=1,
        max_length=1_000,
    )


@dataclass(frozen=True)
class PaymentProfileEvaluation:
    decision: PolicyDecision
    reason: str
    profile_id: str
    profile_version: int
    policy_version: str = MPP_POLICY_VERSION


class AgentPaymentProfilePolicy:
    """Pure profile checks used by the later MPP Policy Engine."""

    @staticmethod
    def evaluate(
        profile: AgentPaymentProfile,
        *,
        agent_id: str,
        service_id: str,
        recipient: str,
        network: str | PaymentProfileNetwork,
        token: str,
        amount_units: int,
        purpose: str = "SERVICE_PAYMENT",
        expires_at: datetime | None = None,
        program_id: str | None = None,
        task_spent_units: int = 0,
        daily_spent_units: int = 0,
        now: datetime | None = None,
    ) -> PaymentProfileEvaluation:
        def deny(reason: str) -> PaymentProfileEvaluation:
            return PaymentProfileEvaluation(
                PolicyDecision.DENY,
                reason,
                profile.profile_id,
                profile.version,
            )

        if profile.status is not PaymentProfileStatus.ACTIVE:
            return deny(f"Payment Profile is not active: {profile.status.value}")
        if profile.rotation_state is not PaymentProfileRotationState.CURRENT:
            return deny(
                f"Payment Profile rotation state is not current: {profile.rotation_state.value}"
            )
        current = _normalise_utc(now or datetime.now(timezone.utc))
        if _normalise_utc(profile.expires_at) <= current:
            return deny("Payment Profile has expired")
        if profile.agent_id != agent_id:
            return deny("Payment Profile is registered to a different Agent")
        if profile.service_id != service_id or service_id not in profile.service_allowlist:
            return deny("Service is outside the Payment Profile allowlist")
        if profile.recipient != recipient or recipient not in profile.recipient_allowlist:
            return deny("Recipient is outside the Payment Profile allowlist")
        network_value = (
            network.value if isinstance(network, PaymentProfileNetwork) else str(network)
        )
        if network_value != profile.network.value:
            return deny("Network is outside the Payment Profile allowlist")
        if token not in profile.token_allowlist:
            return deny("Token is outside the Payment Profile allowlist")
        if purpose not in profile.purpose_allowlist:
            return deny("Payment purpose is outside the Payment Profile allowlist")
        if profile.program_allowlist:
            if program_id is None or program_id not in profile.program_allowlist:
                return deny("Program is outside the Payment Profile allowlist")
        elif program_id is not None:
            return deny("Profile has no allowlisted Program")
        if type(amount_units) is not int or amount_units <= 0:
            return deny("Payment amount must be a positive integer")
        if amount_units > profile.per_payment_limit_units:
            return deny("Payment exceeds the per-payment Profile limit")
        if amount_units + task_spent_units > profile.per_task_limit_units:
            return deny("Payment exceeds the per-Task Profile limit")
        if amount_units + daily_spent_units > profile.daily_limit_units:
            return deny("Payment exceeds the daily Profile limit")
        if task_spent_units < 0 or daily_spent_units < 0:
            return deny("Profile spend counters cannot be negative")
        if expires_at is not None:
            request_expiry = _normalise_utc(expires_at)
            if request_expiry <= current:
                return deny("Payment Challenge has expired")
            if (request_expiry - current).total_seconds() > profile.max_expiry_seconds:
                return deny("Payment Challenge exceeds the Profile expiry limit")
        if profile.approval_mode is PaymentApprovalMode.DENY:
            return deny("Payment Profile is configured to deny payments")
        if (
            profile.approval_mode is PaymentApprovalMode.OWNER_APPROVAL
            or amount_units > profile.auto_approval_limit_units
        ):
            return PaymentProfileEvaluation(
                PolicyDecision.REQUIRE_OWNER_APPROVAL,
                "Payment Profile requires explicit Owner approval",
                profile.profile_id,
                profile.version,
            )
        return PaymentProfileEvaluation(
            PolicyDecision.ALLOW,
            "Payment is within the Payment Profile limits",
            profile.profile_id,
            profile.version,
        )

    @staticmethod
    def ensure_usable(profile: AgentPaymentProfile, **kwargs: Any) -> PaymentProfileEvaluation:
        evaluation = AgentPaymentProfilePolicy.evaluate(profile, **kwargs)
        if evaluation.decision is PolicyDecision.DENY:
            raise PaymentProfileError(evaluation.reason)
        return evaluation


PROFILE_RECORD_COLUMNS = (
    "id",
    "agent_id",
    "version",
    "wallet_id",
    "public_key",
    "network",
    "cluster",
    "service_id",
    "recipient",
    "recipient_kind",
    "token_allowlist",
    "mint_allowlist",
    "service_allowlist",
    "recipient_allowlist",
    "program_allowlist",
    "purpose_allowlist",
    "risk_level",
    "approval_mode",
    "per_payment_limit_units",
    "per_task_limit_units",
    "daily_limit_units",
    "auto_approval_limit_units",
    "max_expiry_seconds",
    "expires_at",
    "status",
    "rotation_state",
    "created_by",
    "owner_approval_id",
    "created_at",
    "updated_at",
)


def _row_values(row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    return dict(zip(PROFILE_RECORD_COLUMNS, row, strict=True))


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


def _profile_from_row(row: Any) -> AgentPaymentProfile:
    values = _row_values(row)
    return AgentPaymentProfile(
        profile_id=str(values["id"]),
        agent_id=str(values["agent_id"]),
        version=int(values["version"]),
        wallet_id=values.get("wallet_id"),
        public_key=values.get("public_key"),
        network=PaymentProfileNetwork(str(values["network"])),
        cluster=str(values.get("cluster") or "LOCAL"),
        service_id=str(values["service_id"]),
        recipient=str(values["recipient"]),
        recipient_kind=str(values.get("recipient_kind") or SERVICE_RECIPIENT_KIND),
        token_allowlist=_json_value(values.get("token_allowlist"), []),
        mint_allowlist=_json_value(values.get("mint_allowlist"), []),
        service_allowlist=_json_value(values.get("service_allowlist"), []),
        recipient_allowlist=_json_value(values.get("recipient_allowlist"), []),
        program_allowlist=_json_value(values.get("program_allowlist"), []),
        purpose_allowlist=_json_value(values.get("purpose_allowlist"), ["SERVICE_PAYMENT"]),
        risk_level=PaymentProfileRiskLevel(str(values.get("risk_level") or "LOW")),
        approval_mode=PaymentApprovalMode(str(values.get("approval_mode") or "OWNER_APPROVAL")),
        per_payment_limit_units=int(values.get("per_payment_limit_units") or 0),
        per_task_limit_units=int(values.get("per_task_limit_units") or 0),
        daily_limit_units=int(values.get("daily_limit_units") or 0),
        auto_approval_limit_units=int(values.get("auto_approval_limit_units") or 0),
        max_expiry_seconds=int(values.get("max_expiry_seconds") or 0),
        expires_at=values["expires_at"],
        status=PaymentProfileStatus(str(values.get("status") or "DISABLED")),
        rotation_state=PaymentProfileRotationState(
            str(values.get("rotation_state") or "CURRENT")
        ),
        created_by=str(values.get("created_by") or "unknown"),
        owner_approval_id=values.get("owner_approval_id"),
        created_at=values.get("created_at"),
        updated_at=values.get("updated_at"),
    )


class AgentPaymentProfileRepository:
    """Transactional persistence for Owner-managed Payment Profiles."""

    def __init__(self, transaction: Any) -> None:
        self.transaction = transaction

    @staticmethod
    def require_owner(actor_type: str) -> None:
        if str(actor_type).upper() != "OWNER":
            raise PaymentProfileAuthorizationError(
                "Payment Profile changes require Owner authority"
            )

    def get(self, profile_id: str, *, for_update: bool = False) -> AgentPaymentProfile | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = self.transaction.fetch_one(
            f"""
            SELECT {', '.join(PROFILE_RECORD_COLUMNS)}
            FROM mvp_agent_payment_profiles
            WHERE id = %s{suffix}
            """,
            (profile_id,),
        )
        return _profile_from_row(row) if row is not None else None

    def require(self, profile_id: str, *, for_update: bool = False) -> AgentPaymentProfile:
        profile = self.get(profile_id, for_update=for_update)
        if profile is None:
            raise PaymentProfileNotFoundError(f"Payment Profile is not registered: {profile_id}")
        return profile

    def list_for_agent(self, agent_id: str) -> tuple[AgentPaymentProfile, ...]:
        rows = self.transaction.fetch_all(
            f"""
            SELECT {', '.join(PROFILE_RECORD_COLUMNS)}
            FROM mvp_agent_payment_profiles
            WHERE agent_id = %s
            ORDER BY service_id ASC, id ASC
            """,
            (agent_id,),
        )
        return tuple(_profile_from_row(row) for row in rows)

    def list_all(self) -> tuple[AgentPaymentProfile, ...]:
        rows = self.transaction.fetch_all(
            f"""
            SELECT {', '.join(PROFILE_RECORD_COLUMNS)}
            FROM mvp_agent_payment_profiles
            ORDER BY agent_id ASC, service_id ASC, id ASC
            """
        )
        return tuple(_profile_from_row(row) for row in rows)

    def find_for_payment(
        self,
        *,
        agent_id: str,
        service_id: str,
        recipient: str,
        network: str | PaymentProfileNetwork,
        token: str,
    ) -> AgentPaymentProfile | None:
        profiles = self.list_for_agent(agent_id)
        network_value = network.value if isinstance(network, PaymentProfileNetwork) else str(network)
        for profile in profiles:
            if profile.status is not PaymentProfileStatus.ACTIVE:
                continue
            if profile.service_id != service_id or profile.recipient != recipient:
                continue
            if profile.network.value != network_value or token not in profile.token_allowlist:
                continue
            return profile
        return None

    def create(
        self,
        *,
        actor_id: str,
        actor_type: str,
        profile: AgentPaymentProfile,
        audit_id: str,
        correlation_id: str,
        reason: str = "Owner created an Agent Payment Profile",
    ) -> AgentPaymentProfile:
        self.require_owner(actor_type)
        if profile.version != 1:
            raise PaymentProfileConcurrencyError("new Payment Profiles must start at version 1")
        if self.get(profile.profile_id) is not None:
            raise PaymentProfileConflictError(
                f"Payment Profile already exists: {profile.profile_id}"
            )
        agent = self.transaction.fetch_one(
            "SELECT id FROM mvp_agents WHERE id = %s",
            (profile.agent_id,),
        )
        if agent is None:
            raise PaymentProfileError(f"Agent is not registered: {profile.agent_id}")
        self._insert_current(profile, actor_id=actor_id)
        created = self.require(profile.profile_id)
        self._append_version(created, changed_by=actor_id, change_type="CREATE")
        self._audit(
            audit_id=audit_id,
            correlation_id=correlation_id,
            actor_id=actor_id,
            actor_type=actor_type,
            action="CREATE_PAYMENT_PROFILE",
            target_id=created.profile_id,
            before_state={},
            after_state=created.public_state(),
            reason=reason,
        )
        return created

    def update(
        self,
        *,
        actor_id: str,
        actor_type: str,
        profile: AgentPaymentProfile,
        expected_version: int,
        audit_id: str,
        correlation_id: str,
        owner_approval_id: str | None = None,
        change_type: str = "UPDATE",
        audit_action: str = "CHANGE_PAYMENT_PROFILE",
        reason: str = "Owner changed an Agent Payment Profile",
    ) -> AgentPaymentProfile:
        self.require_owner(actor_type)
        # Re-validate replacements even when an internal caller constructed
        # them with ``model_copy(update=...)`` (which intentionally skips
        # Pydantic validation).
        profile = AgentPaymentProfile.model_validate(profile.model_dump())
        current = self.require(profile.profile_id, for_update=True)
        if current.version != expected_version or profile.version != expected_version + 1:
            raise PaymentProfileConcurrencyError(
                f"Payment Profile version changed; expected {expected_version}"
            )
        if current.agent_id != profile.agent_id:
            raise PaymentProfileError("Payment Profile Agent identity is immutable")
        if _expands_profile(current, profile) and owner_approval_id is None:
            raise PaymentProfileApprovalRequiredError(
                "limit, allowlist, network, or expiry expansion requires Owner approval"
            )
        if owner_approval_id is not None:
            self._require_approved_request(owner_approval_id, profile.profile_id)
        self._update_current(profile, current=current, actor_id=actor_id, owner_approval_id=owner_approval_id)
        updated = self.require(profile.profile_id)
        if updated.version != expected_version + 1:
            raise PaymentProfileConcurrencyError("Payment Profile update was not applied")
        self._append_version(
            updated,
            changed_by=actor_id,
            change_type=change_type,
            owner_approval_id=owner_approval_id,
        )
        self._audit(
            audit_id=audit_id,
            correlation_id=correlation_id,
            actor_id=actor_id,
            actor_type=actor_type,
            action=audit_action,
            target_id=updated.profile_id,
            before_state=current.public_state(),
            after_state=updated.public_state(),
            reason=reason,
        )
        return updated

    def set_status(
        self,
        *,
        actor_id: str,
        actor_type: str,
        profile_id: str,
        status: PaymentProfileStatus,
        expected_version: int,
        audit_id: str,
        correlation_id: str,
        reason: str,
    ) -> AgentPaymentProfile:
        self.require_owner(actor_type)
        if not reason.strip():
            raise PaymentProfileError("profile status reason is required")
        current = self.require(profile_id, for_update=True)
        if current.version != expected_version:
            raise PaymentProfileConcurrencyError(
                f"Payment Profile version changed; expected {expected_version}"
            )
        if status is PaymentProfileStatus.ACTIVE and _normalise_utc(current.expires_at) <= datetime.now(timezone.utc):
            raise PaymentProfileError("an expired Payment Profile cannot be activated")
        self.transaction.execute(
            """
            UPDATE mvp_agent_payment_profiles
            SET status = %s, version = version + 1, updated_at = now()
            WHERE id = %s AND version = %s
            """,
            (status.value, profile_id, expected_version),
        )
        updated = self.require(profile_id)
        if updated.version != expected_version + 1:
            raise PaymentProfileConcurrencyError("Payment Profile status update was not applied")
        self._append_version(updated, changed_by=actor_id, change_type="STATUS")
        self._audit(
            audit_id=audit_id,
            correlation_id=correlation_id,
            actor_id=actor_id,
            actor_type=actor_type,
            action="CHANGE_PAYMENT_PROFILE_STATUS",
            target_id=profile_id,
            before_state=current.public_state(),
            after_state=updated.public_state(),
            reason=reason,
        )
        return updated

    def rotate(
        self,
        *,
        actor_id: str,
        actor_type: str,
        profile_id: str,
        expected_version: int,
        public_key: str,
        wallet_id: str | None,
        audit_id: str,
        correlation_id: str,
        reason: str = "Owner rotated the Payment Profile public identity",
    ) -> AgentPaymentProfile:
        self.require_owner(actor_type)
        current = self.require(profile_id)
        if current.version != expected_version:
            raise PaymentProfileConcurrencyError(
                f"Payment Profile version changed; expected {expected_version}"
            )
        replacement_values = current.model_dump()
        replacement_values.update(
            {
                "version": expected_version + 1,
                "public_key": public_key,
                "wallet_id": wallet_id,
                "rotation_state": PaymentProfileRotationState.CURRENT,
            }
        )
        replacement = AgentPaymentProfile(**replacement_values)
        return self.update(
            actor_id=actor_id,
            actor_type=actor_type,
            profile=replacement,
            expected_version=expected_version,
            audit_id=audit_id,
            correlation_id=correlation_id,
            change_type="ROTATE",
            audit_action="ROTATE_PAYMENT_PROFILE",
            reason=reason,
        )

    def _insert_current(self, profile: AgentPaymentProfile, *, actor_id: str) -> None:
        values = _profile_sql_values(profile, actor_id=actor_id, owner_approval_id=profile.owner_approval_id)
        columns = """
            id, agent_id, version, wallet_id, public_key, network, cluster, service_id,
            recipient, recipient_kind, token_allowlist, mint_allowlist, service_allowlist,
            recipient_allowlist, program_allowlist, purpose_allowlist, risk_level,
            approval_mode, per_payment_limit_units, per_task_limit_units, daily_limit_units,
            auto_approval_limit_units, max_expiry_seconds, expires_at, status,
            rotation_state, created_by, owner_approval_id
        """
        self.transaction.execute(
            f"""
            INSERT INTO mvp_agent_payment_profiles ({' '.join(columns.split())})
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s)
            """,
            values,
        )

    def _update_current(
        self,
        profile: AgentPaymentProfile,
        *,
        current: AgentPaymentProfile,
        actor_id: str,
        owner_approval_id: str | None,
    ) -> None:
        values = _profile_sql_values(
            profile,
            actor_id=current.created_by,
            owner_approval_id=owner_approval_id,
        )
        self.transaction.execute(
            """
            UPDATE mvp_agent_payment_profiles
            SET wallet_id = %s, public_key = %s, network = %s, cluster = %s,
                service_id = %s, recipient = %s, recipient_kind = %s,
                token_allowlist = %s::jsonb, mint_allowlist = %s::jsonb,
                service_allowlist = %s::jsonb, recipient_allowlist = %s::jsonb,
                program_allowlist = %s::jsonb, purpose_allowlist = %s::jsonb,
                risk_level = %s, approval_mode = %s,
                per_payment_limit_units = %s, per_task_limit_units = %s,
                daily_limit_units = %s, auto_approval_limit_units = %s,
                max_expiry_seconds = %s, expires_at = %s, status = %s,
                rotation_state = %s, owner_approval_id = %s,
                version = version + 1, updated_at = now()
            WHERE id = %s AND version = %s
            """,
            (
                values[3],
                values[4],
                values[5],
                values[6],
                values[7],
                values[8],
                values[9],
                values[10],
                values[11],
                values[12],
                values[13],
                values[14],
                values[15],
                values[16],
                values[17],
                values[18],
                values[19],
                values[20],
                values[21],
                values[22],
                values[23],
                values[24],
                values[25],
                owner_approval_id,
                profile.profile_id,
                current.version,
            ),
        )

    def _require_approved_request(self, approval_id: str, profile_id: str) -> None:
        row = self.transaction.fetch_one(
            """
            SELECT id
            FROM approval_requests
            WHERE id = %s AND target_id = %s AND owner_decision = 'APPROVE'
            """,
            (approval_id, profile_id),
        )
        if row is None:
            raise PaymentProfileApprovalRequiredError(
                "Owner approval request is missing or not approved"
            )

    def _append_version(
        self,
        profile: AgentPaymentProfile,
        *,
        changed_by: str,
        change_type: str,
        owner_approval_id: str | None = None,
    ) -> None:
        self.transaction.execute(
            """
            INSERT INTO mvp_agent_payment_profile_versions
              (profile_id, version, snapshot, changed_by, change_type, owner_approval_id)
            VALUES (%s, %s, %s::jsonb, %s, %s, %s)
            """,
            (
                profile.profile_id,
                profile.version,
                json.dumps(profile.public_state(), ensure_ascii=False, default=str),
                changed_by,
                change_type,
                owner_approval_id,
            ),
        )

    def _audit(
        self,
        *,
        audit_id: str,
        correlation_id: str,
        actor_id: str,
        actor_type: str,
        action: str,
        target_id: str,
        before_state: Mapping[str, Any],
        after_state: Mapping[str, Any],
        reason: str,
    ) -> None:
        AuditWriter.append(
            self.transaction,
            AuditEvent(
                event_id=audit_id,
                event_version=1,
                event_type=f"PAYMENT_PROFILE_{action.removeprefix('CREATE_').removeprefix('CHANGE_')}",
                actor_id=actor_id,
                actor_type=actor_type,
                action=action,
                target_type="PAYMENT_PROFILE",
                target_id=target_id,
                before_state=before_state,
                after_state=after_state,
                policy_result="ALLOW",
                reason=reason,
                correlation_id=correlation_id,
                transaction_id=correlation_id,
            ),
        )


def _profile_sql_values(
    profile: AgentPaymentProfile,
    *,
    actor_id: str,
    owner_approval_id: str | None,
) -> tuple[Any, ...]:
    return (
        profile.profile_id,
        profile.agent_id,
        profile.version,
        profile.wallet_id,
        profile.public_key,
        profile.network.value,
        profile.cluster,
        profile.service_id,
        profile.recipient,
        profile.recipient_kind,
        json.dumps(profile.token_allowlist),
        json.dumps(profile.mint_allowlist),
        json.dumps(profile.service_allowlist),
        json.dumps(profile.recipient_allowlist),
        json.dumps(profile.program_allowlist),
        json.dumps(profile.purpose_allowlist),
        profile.risk_level.value,
        profile.approval_mode.value,
        profile.per_payment_limit_units,
        profile.per_task_limit_units,
        profile.daily_limit_units,
        profile.auto_approval_limit_units,
        profile.max_expiry_seconds,
        profile.expires_at,
        profile.status.value,
        profile.rotation_state.value,
        actor_id,
        owner_approval_id,
    )


def _expands_profile(current: AgentPaymentProfile, replacement: AgentPaymentProfile) -> bool:
    """Return whether a replacement broadens the security envelope."""

    current_network = current.network.value
    replacement_network = replacement.network.value
    if current_network != replacement_network:
        return True
    for old, new in (
        (set(current.token_allowlist), set(replacement.token_allowlist)),
        (set(current.mint_allowlist), set(replacement.mint_allowlist)),
        (set(current.service_allowlist), set(replacement.service_allowlist)),
        (set(current.recipient_allowlist), set(replacement.recipient_allowlist)),
        (set(current.program_allowlist), set(replacement.program_allowlist)),
    ):
        if not new <= old:
            return True
    if (
        replacement.per_payment_limit_units > current.per_payment_limit_units
        or replacement.per_task_limit_units > current.per_task_limit_units
        or replacement.daily_limit_units > current.daily_limit_units
        or replacement.auto_approval_limit_units > current.auto_approval_limit_units
        or replacement.max_expiry_seconds > current.max_expiry_seconds
        or _normalise_utc(replacement.expires_at) > _normalise_utc(current.expires_at)
    ):
        return True
    approval_order = {
        PaymentApprovalMode.DENY: 0,
        PaymentApprovalMode.OWNER_APPROVAL: 1,
        PaymentApprovalMode.AUTO_ALLOW: 2,
    }
    return approval_order[replacement.approval_mode] > approval_order[current.approval_mode]


# Backwards-compatible repository name for callers that use the shorter term.
PaymentProfileRepository = AgentPaymentProfileRepository
