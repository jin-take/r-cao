"""Persistent Agent Registry contracts and authorization checks.

The registry is the canonical source for an Agent's identity, role,
capabilities, execution scope, and status.  Payment profiles are deliberately
not part of an Agent's authority; they are a separate relation used by the
future MPP policy boundary.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from .audit import AuditEvent, AuditWriter


class AgentRegistryError(ValueError):
    """Base class for Registry validation and persistence errors."""


class AgentNotRegisteredError(AgentRegistryError):
    """The requested Agent is not in the canonical Registry."""


class AgentNotEligibleError(AgentRegistryError):
    """The Agent is registered but outside the requested execution scope."""


class RegistryAuthorizationError(AgentRegistryError):
    """The caller is not allowed to change Registry state."""


class MembershipError(AgentRegistryError):
    """The Agent is not a valid member of the requested Task."""


class DelegationError(AgentRegistryError):
    """The delegation grant is invalid or outside its scope."""


RISK_LEVEL_ORDER = {
    "LOW": 0,
    "MEDIUM": 1,
    "HIGH": 2,
    "CRITICAL": 3,
}


AGENT_RECORD_COLUMNS = (
    "id",
    "identity_id",
    "name",
    "role",
    "organization_layer",
    "mission",
    "responsibilities",
    "authority",
    "prohibited_actions",
    "reports_to",
    "agent_type",
    "status",
    "version",
    "model",
    "provider",
    "prompt_version",
    "capability_hash",
    "allowed_tools",
    "network_scope",
    "budget_scope",
    "risk_scope",
    "budget_limit_lamports",
    "expires_at",
)


def _values(record: Mapping[str, Any] | tuple[Any, ...], columns: tuple[str, ...]) -> dict[str, Any]:
    if isinstance(record, Mapping):
        return dict(record)
    return dict(zip(columns, record, strict=True))


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise AgentRegistryError("Registry JSON field is invalid") from exc
    return value


def _as_tuple(value: Any) -> tuple[str, ...]:
    parsed = _json_value(value, [])
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise AgentRegistryError("Registry list field must contain strings")
    return tuple(parsed)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    parsed = _json_value(value, {})
    if not isinstance(parsed, dict):
        raise AgentRegistryError("Registry scope field must be an object")
    return parsed


@dataclass(frozen=True)
class RegisteredAgent:
    agent_id: str
    identity_id: str
    name: str
    role: str
    organization_layer: str
    mission: str
    responsibilities: tuple[str, ...]
    authority: tuple[str, ...]
    prohibited_actions: tuple[str, ...]
    reports_to: str
    agent_type: str
    status: str
    version: int
    model: str
    provider: str
    prompt_version: str
    capability_hash: str
    allowed_tools: tuple[str, ...]
    network_scope: tuple[str, ...]
    budget_scope: Mapping[str, Any] = field(default_factory=dict)
    risk_scope: Mapping[str, Any] = field(default_factory=dict)
    budget_limit_lamports: int = 0
    expires_at: datetime | str | None = None

    @classmethod
    def from_record(cls, record: Mapping[str, Any] | tuple[Any, ...]) -> "RegisteredAgent":
        values = _values(record, AGENT_RECORD_COLUMNS)
        return cls(
            agent_id=str(values["id"]),
            identity_id=str(values.get("identity_id") or values["id"]),
            name=str(values["name"]),
            role=str(values["role"]),
            organization_layer=str(values.get("organization_layer") or "VALUE_CREATION"),
            mission=str(values["mission"]),
            responsibilities=_as_tuple(values.get("responsibilities")),
            authority=_as_tuple(values.get("authority")),
            prohibited_actions=_as_tuple(values.get("prohibited_actions")),
            reports_to=str(values["reports_to"]),
            agent_type=str(values["agent_type"]),
            status=str(values["status"]),
            version=int(values["version"]),
            model=str(values["model"]),
            provider=str(values.get("provider") or "TEST"),
            prompt_version=str(values.get("prompt_version") or "unversioned"),
            capability_hash=str(values["capability_hash"]),
            allowed_tools=_as_tuple(values.get("allowed_tools")),
            network_scope=_as_tuple(values.get("network_scope")),
            budget_scope=_as_mapping(values.get("budget_scope")),
            risk_scope=_as_mapping(values.get("risk_scope")),
            budget_limit_lamports=int(values.get("budget_limit_lamports") or 0),
            expires_at=values.get("expires_at"),
        )

    def to_state(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "identity_id": self.identity_id,
            "name": self.name,
            "role": self.role,
            "organization_layer": self.organization_layer,
            "mission": self.mission,
            "responsibilities": list(self.responsibilities),
            "authority": list(self.authority),
            "prohibited_actions": list(self.prohibited_actions),
            "reports_to": self.reports_to,
            "agent_type": self.agent_type,
            "status": self.status,
            "version": self.version,
            "model": self.model,
            "provider": self.provider,
            "prompt_version": self.prompt_version,
            "capability_hash": self.capability_hash,
            "allowed_tools": list(self.allowed_tools),
            "network_scope": list(self.network_scope),
            "budget_scope": dict(self.budget_scope),
            "risk_scope": dict(self.risk_scope),
            "budget_limit_lamports": self.budget_limit_lamports,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True)
class AgentRegistration:
    agent_id: str
    name: str
    role: str
    mission: str
    reports_to: str
    agent_type: str
    capability_hash: str
    identity_id: str | None = None
    organization_layer: str = "VALUE_CREATION"
    responsibilities: tuple[str, ...] = ()
    authority: tuple[str, ...] = ()
    prohibited_actions: tuple[str, ...] = ()
    status: str = "DRAFT"
    model: str = "policy-bound"
    provider: str = "TEST"
    prompt_version: str = "unversioned"
    allowed_tools: tuple[str, ...] = ()
    network_scope: tuple[str, ...] = ("OFFCHAIN",)
    budget_scope: Mapping[str, Any] = field(default_factory=dict)
    risk_scope: Mapping[str, Any] = field(default_factory=dict)
    budget_limit_lamports: int = 0
    expires_at: datetime | str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "agent_id",
            "name",
            "role",
            "mission",
            "reports_to",
            "agent_type",
            "capability_hash",
        ):
            if not getattr(self, field_name):
                raise AgentRegistryError(f"{field_name} is required")
        if self.organization_layer not in {
            "VALUE_CREATION",
            "VALUE_PROTECTION",
            "VALUE_EVOLUTION",
        }:
            raise AgentRegistryError("invalid organization_layer")
        if self.status not in {"ACTIVE", "SUSPENDED", "RETIRED", "DRAFT"}:
            raise AgentRegistryError("invalid Agent status")
        if self.budget_limit_lamports < 0:
            raise AgentRegistryError("budget_limit_lamports cannot be negative")


@dataclass(frozen=True)
class AgentMembership:
    task_id: str
    agent_id: str
    membership_role: str
    assigned_by: str
    active: bool
    expires_at: datetime | str | None = None

    @classmethod
    def from_record(cls, record: Mapping[str, Any] | tuple[Any, ...]) -> "AgentMembership":
        values = _values(
            record,
            ("task_id", "agent_id", "membership_role", "assigned_by", "active", "expires_at"),
        )
        return cls(
            task_id=str(values["task_id"]),
            agent_id=str(values["agent_id"]),
            membership_role=str(values["membership_role"]),
            assigned_by=str(values["assigned_by"]),
            active=bool(values["active"]),
            expires_at=values.get("expires_at"),
        )


@dataclass(frozen=True)
class DelegationGrant:
    delegation_id: str
    parent_agent_id: str
    child_agent_id: str
    task_id: str | None
    allowed_scope: tuple[str, ...]
    budget_limit_lamports: int
    risk_scope: Mapping[str, Any]
    status: str
    expires_at: datetime | str

    @classmethod
    def from_record(cls, record: Mapping[str, Any] | tuple[Any, ...]) -> "DelegationGrant":
        values = _values(
            record,
            (
                "id",
                "parent_agent_id",
                "child_agent_id",
                "task_id",
                "allowed_scope",
                "budget_limit_lamports",
                "risk_scope",
                "status",
                "expires_at",
            ),
        )
        expires_at = values.get("expires_at")
        if expires_at is None:
            raise DelegationError("delegation expires_at is required")
        return cls(
            delegation_id=str(values["id"]),
            parent_agent_id=str(values["parent_agent_id"]),
            child_agent_id=str(values["child_agent_id"]),
            task_id=str(values["task_id"]) if values.get("task_id") is not None else None,
            allowed_scope=_as_tuple(values.get("allowed_scope")),
            budget_limit_lamports=int(values["budget_limit_lamports"]),
            risk_scope=_as_mapping(values.get("risk_scope")),
            status=str(values["status"]),
            expires_at=expires_at,
        )


@dataclass(frozen=True)
class DelegationCommand:
    delegation_id: str
    parent_agent_id: str
    child_agent_id: str
    task_id: str | None
    allowed_scope: tuple[str, ...]
    budget_limit_lamports: int
    risk_scope: Mapping[str, Any]
    expires_at: datetime | str

    def __post_init__(self) -> None:
        if not self.delegation_id or not self.parent_agent_id or not self.child_agent_id:
            raise AgentRegistryError("delegation identity fields are required")
        if self.parent_agent_id == self.child_agent_id:
            raise DelegationError("an Agent cannot delegate to itself")
        if not self.allowed_scope:
            raise DelegationError("delegation allowed_scope is required")
        if self.budget_limit_lamports < 0:
            raise DelegationError("delegation budget cannot be negative")


def _as_utc(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class AgentRegistryPolicy:
    """Pure validation for Registry-backed execution and delegation."""

    @staticmethod
    def require_owner(actor_type: str) -> None:
        if actor_type.upper() != "OWNER":
            raise RegistryAuthorizationError("Registry changes require Owner authority")

    @staticmethod
    def ensure_active(agent: RegisteredAgent, *, now: datetime | None = None) -> None:
        if agent.status != "ACTIVE":
            raise AgentNotEligibleError(
                f"Agent {agent.agent_id} is not eligible while status is {agent.status}"
            )
        if agent.expires_at is not None:
            current = now or datetime.now(timezone.utc)
            if _as_utc(agent.expires_at) <= _as_utc(current):
                raise AgentNotEligibleError(f"Agent {agent.agent_id} registration has expired")

    @staticmethod
    def _ensure_risk_scope(
        scope: Mapping[str, Any],
        requested_risk: str | None,
        *,
        error_type: type[AgentRegistryError],
    ) -> None:
        """Require an explicit, persisted allow rule for classified work."""

        if requested_risk is None:
            return
        normalized = requested_risk.upper()
        if normalized not in RISK_LEVEL_ORDER:
            raise error_type(f"invalid risk classification: {requested_risk}")
        if not scope:
            raise error_type("risk classification is not allowed by an empty risk scope")

        allowed = scope.get("allowed", scope.get("levels"))
        if allowed is not None:
            if not isinstance(allowed, (list, tuple, set)):
                raise error_type("risk scope allowed levels must be a list")
            normalized_allowed = {str(item).upper() for item in allowed}
            if normalized not in normalized_allowed:
                raise error_type(
                    f"risk classification is outside the allowed scope: {requested_risk}"
                )

        maximum = scope.get("max")
        if maximum is not None:
            normalized_maximum = str(maximum).upper()
            if normalized_maximum not in RISK_LEVEL_ORDER:
                raise error_type(f"invalid maximum risk classification: {maximum}")
            if RISK_LEVEL_ORDER[normalized] > RISK_LEVEL_ORDER[normalized_maximum]:
                raise error_type(
                    f"risk classification exceeds the permitted maximum: {requested_risk}"
                )

        if allowed is None and maximum is None:
            raise error_type("risk scope does not define an allow rule")

    @staticmethod
    def ensure_can_participate(
        agent: RegisteredAgent,
        *,
        task_id: str | None = None,
        membership: AgentMembership | None = None,
        delegation: DelegationGrant | None = None,
        action: str | None = None,
        required_capability: str | None = None,
        required_tool: str | None = None,
        network: str | None = None,
        amount_lamports: int = 0,
        risk_level: str | None = None,
        now: datetime | None = None,
    ) -> None:
        AgentRegistryPolicy.ensure_active(agent, now=now)
        if amount_lamports < 0:
            raise AgentNotEligibleError("amount_lamports cannot be negative")
        if required_capability is not None and agent.capability_hash != required_capability:
            raise AgentNotEligibleError("Agent capability does not match the requested capability")
        if required_tool is not None and required_tool not in agent.allowed_tools:
            raise AgentNotEligibleError(f"Tool is outside the Agent allowlist: {required_tool}")
        if network is not None and network not in agent.network_scope:
            raise AgentNotEligibleError(f"Network is outside the Agent scope: {network}")

        # budget_limit_lamports is the hard persisted limit.  budget_scope can
        # narrow it further, but an omitted scope value must never mean
        # unlimited spending.
        if amount_lamports > agent.budget_limit_lamports:
            raise AgentNotEligibleError("Requested amount exceeds the Agent budget limit")
        max_budget = agent.budget_scope.get("max_lamports")
        if max_budget is not None and (
            type(max_budget) is not int or max_budget < 0
        ):
            raise AgentNotEligibleError("Agent budget scope has an invalid maximum")
        if max_budget is not None and amount_lamports > max_budget:
            raise AgentNotEligibleError("Requested amount exceeds the Agent budget scope")
        AgentRegistryPolicy._ensure_risk_scope(
            agent.risk_scope,
            risk_level,
            error_type=AgentNotEligibleError,
        )
        if task_id is not None:
            if membership is None or membership.agent_id != agent.agent_id or membership.task_id != task_id:
                raise MembershipError("Agent is not a member of the requested Task")
            if not membership.active:
                raise MembershipError("Agent Task membership is inactive")
            if membership.expires_at is not None:
                current = now or datetime.now(timezone.utc)
                if _as_utc(membership.expires_at) <= _as_utc(current):
                    raise MembershipError("Agent Task membership has expired")

        if delegation is not None:
            if delegation.child_agent_id != agent.agent_id:
                raise DelegationError("Delegation recipient does not match the Agent")
            if delegation.status != "ACTIVE":
                raise DelegationError("Delegation is not active")
            if delegation.task_id not in {None, task_id}:
                raise DelegationError("Delegation is outside the requested Task")
            current = now or datetime.now(timezone.utc)
            if _as_utc(delegation.expires_at) <= _as_utc(current):
                raise DelegationError("Delegation has expired")
            if action is not None and action not in delegation.allowed_scope and "*" not in delegation.allowed_scope:
                raise DelegationError(f"Action is outside the delegation scope: {action}")
            if amount_lamports > delegation.budget_limit_lamports:
                raise DelegationError("Requested amount exceeds the delegation budget")
            AgentRegistryPolicy._ensure_risk_scope(
                delegation.risk_scope,
                risk_level,
                error_type=DelegationError,
            )


class AgentRegistryRepository:
    """Read and mutate the canonical Registry through a Unit of Work."""

    def __init__(self, transaction: Any) -> None:
        self.transaction = transaction

    def get_agent(self, agent_id: str) -> RegisteredAgent | None:
        row = self.transaction.fetch_one(
            f"""
            SELECT {', '.join(AGENT_RECORD_COLUMNS)}
            FROM mvp_agents
            WHERE id = %s
            """,
            (agent_id,),
        )
        return RegisteredAgent.from_record(row) if row is not None else None

    def require_agent(self, agent_id: str) -> RegisteredAgent:
        agent = self.get_agent(agent_id)
        if agent is None:
            raise AgentNotRegisteredError(f"Agent is not registered: {agent_id}")
        return agent

    def list_agents(self, *, status: str | None = None) -> tuple[RegisteredAgent, ...]:
        if status is None:
            rows = self.transaction.fetch_all(
                f"SELECT {', '.join(AGENT_RECORD_COLUMNS)} FROM mvp_agents ORDER BY name ASC",
            )
        else:
            rows = self.transaction.fetch_all(
                f"SELECT {', '.join(AGENT_RECORD_COLUMNS)} FROM mvp_agents WHERE status = %s::mvp_agent_status ORDER BY name ASC",
                (status,),
            )
        return tuple(RegisteredAgent.from_record(row) for row in rows)

    def get_membership(self, task_id: str, agent_id: str) -> AgentMembership | None:
        row = self.transaction.fetch_one(
            """
            SELECT task_id, agent_id, membership_role, assigned_by, active, expires_at
            FROM mvp_agent_memberships
            WHERE task_id = %s AND agent_id = %s
            """,
            (task_id, agent_id),
        )
        return AgentMembership.from_record(row) if row is not None else None

    def get_delegation(self, delegation_id: str) -> DelegationGrant | None:
        row = self.transaction.fetch_one(
            """
            SELECT id, parent_agent_id, child_agent_id, task_id, allowed_scope,
                   budget_limit_lamports, risk_scope, status, expires_at
            FROM mvp_agent_delegations
            WHERE id = %s
            """,
            (delegation_id,),
        )
        return DelegationGrant.from_record(row) if row is not None else None

    def ensure_can_participate(
        self,
        agent_id: str,
        *,
        task_id: str | None = None,
        delegation_id: str | None = None,
        action: str | None = None,
        required_capability: str | None = None,
        required_tool: str | None = None,
        network: str | None = None,
        amount_lamports: int = 0,
        risk_level: str | None = None,
    ) -> RegisteredAgent:
        agent = self.require_agent(agent_id)
        membership = self.get_membership(task_id, agent_id) if task_id is not None else None
        delegation = self.get_delegation(delegation_id) if delegation_id is not None else None
        if delegation_id is not None and delegation is None:
            raise DelegationError(f"Delegation is not registered: {delegation_id}")
        AgentRegistryPolicy.ensure_can_participate(
            agent,
            task_id=task_id,
            membership=membership,
            delegation=delegation,
            action=action,
            required_capability=required_capability,
            required_tool=required_tool,
            network=network,
            amount_lamports=amount_lamports,
            risk_level=risk_level,
        )
        return agent

    def register_agent(
        self,
        *,
        actor_id: str,
        actor_type: str,
        registration: AgentRegistration,
        audit_id: str,
        correlation_id: str,
        reason: str = "Owner registered Agent",
    ) -> RegisteredAgent:
        AgentRegistryPolicy.require_owner(actor_type)
        if self.get_agent(registration.agent_id) is not None:
            raise AgentRegistryError(f"Agent is already registered: {registration.agent_id}")
        self.transaction.execute(
            """
            INSERT INTO mvp_agents
              (id, identity_id, name, role, organization_layer, mission,
               responsibilities, authority, prohibited_actions, reports_to,
               agent_type, status, model, provider, prompt_version,
               capability_hash, allowed_tools, network_scope, budget_scope,
               risk_scope, budget_limit_lamports, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb,
                    %s, %s::mvp_agent_type, %s::mvp_agent_status, %s, %s,
                    %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s)
            """,
            (
                registration.agent_id,
                registration.identity_id or registration.agent_id,
                registration.name,
                registration.role,
                registration.organization_layer,
                registration.mission,
                json.dumps(registration.responsibilities),
                json.dumps(registration.authority),
                json.dumps(registration.prohibited_actions),
                registration.reports_to,
                registration.agent_type,
                registration.status,
                registration.model,
                registration.provider,
                registration.prompt_version,
                registration.capability_hash,
                json.dumps(registration.allowed_tools),
                json.dumps(registration.network_scope),
                json.dumps(registration.budget_scope),
                json.dumps(registration.risk_scope),
                registration.budget_limit_lamports,
                registration.expires_at,
            ),
        )
        agent = self.require_agent(registration.agent_id)
        self._record_change(
            agent_id=agent.agent_id,
            change_type="REGISTER",
            before_state={},
            after_state=agent.to_state(),
            changed_by=actor_id,
            audit_event_id=audit_id,
        )
        self._audit(
            audit_id=audit_id,
            correlation_id=correlation_id,
            actor_id=actor_id,
            actor_type=actor_type,
            action="REGISTER_AGENT",
            target_id=agent.agent_id,
            before_state={},
            after_state=agent.to_state(),
            reason=reason,
        )
        return agent

    def set_status(
        self,
        *,
        actor_id: str,
        actor_type: str,
        agent_id: str,
        status: str,
        audit_id: str,
        correlation_id: str,
        reason: str = "Owner changed Agent status",
    ) -> RegisteredAgent:
        AgentRegistryPolicy.require_owner(actor_type)
        if status not in {"ACTIVE", "SUSPENDED", "RETIRED", "DRAFT"}:
            raise AgentRegistryError("invalid Agent status")
        before = self.require_agent(agent_id)
        self.transaction.execute(
            """
            UPDATE mvp_agents
            SET status = %s::mvp_agent_status, version = version + 1, updated_at = now()
            WHERE id = %s
            """,
            (status, agent_id),
        )
        after = self.require_agent(agent_id)
        self._record_change(
            agent_id=agent_id,
            change_type="STATUS",
            before_state=before.to_state(),
            after_state=after.to_state(),
            changed_by=actor_id,
            audit_event_id=audit_id,
        )
        self._audit(
            audit_id=audit_id,
            correlation_id=correlation_id,
            actor_id=actor_id,
            actor_type=actor_type,
            action="CHANGE_AGENT_STATUS",
            target_id=agent_id,
            before_state=before.to_state(),
            after_state=after.to_state(),
            reason=reason,
        )
        return after

    def assign_membership(
        self,
        *,
        actor_id: str,
        actor_type: str,
        task_id: str,
        agent_id: str,
        membership_role: str,
        expires_at: datetime | str | None,
        audit_id: str,
        correlation_id: str,
        reason: str = "Owner assigned Agent to Task",
    ) -> AgentMembership:
        AgentRegistryPolicy.require_owner(actor_type)
        agent = self.require_agent(agent_id)
        AgentRegistryPolicy.ensure_active(agent)
        if not membership_role:
            raise MembershipError("membership_role is required")
        before_membership = self.get_membership(task_id, agent_id)
        self.transaction.execute(
            """
            INSERT INTO mvp_agent_memberships
              (task_id, agent_id, membership_role, assigned_by, active, expires_at)
            VALUES (%s, %s, %s, %s, TRUE, %s)
            ON CONFLICT (task_id, agent_id) DO UPDATE SET
              membership_role = EXCLUDED.membership_role,
              assigned_by = EXCLUDED.assigned_by,
              active = TRUE,
              expires_at = EXCLUDED.expires_at
            """,
            (task_id, agent_id, membership_role, actor_id, expires_at),
        )
        membership = self.get_membership(task_id, agent_id)
        if membership is None:
            raise MembershipError("Task membership was not persisted")
        after_state = {
            "task_id": task_id,
            "agent_id": agent_id,
            "membership_role": membership_role,
            "active": True,
            "expires_at": expires_at,
        }
        before_state = (
            {
                "task_id": before_membership.task_id,
                "agent_id": before_membership.agent_id,
                "membership_role": before_membership.membership_role,
                "active": before_membership.active,
                "expires_at": before_membership.expires_at,
            }
            if before_membership is not None
            else {}
        )
        self._record_change(
            agent_id=agent_id,
            change_type="TASK_MEMBERSHIP",
            before_state=before_state,
            after_state=after_state,
            changed_by=actor_id,
            audit_event_id=audit_id,
        )
        self._audit(
            audit_id=audit_id,
            correlation_id=correlation_id,
            actor_id=actor_id,
            actor_type=actor_type,
            action="ASSIGN_AGENT_TASK_MEMBERSHIP",
            target_id=agent_id,
            before_state=before_state,
            after_state=after_state,
            reason=reason,
            task_id=task_id,
        )
        return membership

    def create_delegation(
        self,
        *,
        actor_id: str,
        actor_type: str,
        command: DelegationCommand,
        audit_id: str,
        correlation_id: str,
        reason: str = "Owner created Agent delegation",
    ) -> DelegationGrant:
        AgentRegistryPolicy.require_owner(actor_type)
        parent = self.require_agent(command.parent_agent_id)
        child = self.require_agent(command.child_agent_id)
        AgentRegistryPolicy.ensure_active(parent)
        AgentRegistryPolicy.ensure_active(child)
        if _as_utc(command.expires_at) <= datetime.now(timezone.utc):
            raise DelegationError("delegation expires_at must be in the future")
        self.transaction.execute(
            """
            INSERT INTO mvp_agent_delegations
              (id, parent_agent_id, child_agent_id, task_id, allowed_scope,
               budget_limit_lamports, risk_scope, status, expires_at, created_by)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, 'ACTIVE', %s, %s)
            """,
            (
                command.delegation_id,
                command.parent_agent_id,
                command.child_agent_id,
                command.task_id,
                json.dumps(command.allowed_scope),
                command.budget_limit_lamports,
                json.dumps(command.risk_scope),
                command.expires_at,
                actor_id,
            ),
        )
        delegation = self.get_delegation(command.delegation_id)
        if delegation is None:
            raise DelegationError("delegation was not persisted")
        after_state = {
            "delegation_id": command.delegation_id,
            "parent_agent_id": command.parent_agent_id,
            "child_agent_id": command.child_agent_id,
            "task_id": command.task_id,
            "allowed_scope": list(command.allowed_scope),
            "budget_limit_lamports": command.budget_limit_lamports,
            "risk_scope": dict(command.risk_scope),
            "status": "ACTIVE",
            "expires_at": command.expires_at,
        }
        self._record_change(
            agent_id=command.child_agent_id,
            change_type="DELEGATION",
            before_state={},
            after_state=after_state,
            changed_by=actor_id,
            audit_event_id=audit_id,
        )
        self._audit(
            audit_id=audit_id,
            correlation_id=correlation_id,
            actor_id=actor_id,
            actor_type=actor_type,
            action="CREATE_AGENT_DELEGATION",
            target_id=command.delegation_id,
            before_state={},
            after_state=after_state,
            reason=reason,
            task_id=command.task_id,
        )
        return delegation

    def _record_change(
        self,
        *,
        agent_id: str,
        change_type: str,
        before_state: Mapping[str, Any],
        after_state: Mapping[str, Any],
        changed_by: str,
        audit_event_id: str,
    ) -> None:
        self.transaction.execute(
            """
            INSERT INTO mvp_agent_change_history
              (id, agent_id, change_type, before_state, after_state, changed_by, audit_event_id)
            VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
            """,
            (
                audit_event_id,
                agent_id,
                change_type,
                json.dumps(before_state, default=str, sort_keys=True),
                json.dumps(after_state, default=str, sort_keys=True),
                changed_by,
                audit_event_id,
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
        task_id: str | None = None,
    ) -> None:
        AuditWriter.append(
            self.transaction,
            AuditEvent(
                event_id=audit_id,
                event_version=1,
                event_type="AGENT_REGISTRY_CHANGE",
                actor_id=actor_id,
                actor_type=actor_type,
                action=action,
                target_type="AGENT" if action != "CREATE_AGENT_DELEGATION" else "DELEGATION",
                target_id=target_id,
                before_state=before_state,
                after_state=after_state,
                policy_result="ALLOW",
                reason=reason,
                correlation_id=correlation_id,
                transaction_id=correlation_id,
                task_id=task_id,
            ),
        )
