"""Owner-Directed MVP domain model and in-memory control plane.

The repository is intentionally split into two boundaries:

* this module owns the MVP workflow and its safety invariants; and
* the Next.js application is a presentation/read-model client.

The store is an executable Phase 1 reference implementation.  It is designed
to be replaced by a PostgreSQL repository without changing the command
contracts.  It never signs a transaction, calls an external service, or
creates an Agent-to-Agent reward transfer.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel, Field

from .auth import ActorContext, ActorType
from .models import AgentRole
from .policy import (
    PolicyAction,
    PolicyDecision as ConstitutionalPolicyDecision,
    evaluate_policy,
)


OWNER_ID = "owner-local"
LAMPORTS_PER_SOL = 1_000_000_000


class AgentType(str, Enum):
    EXECUTIVE = "EXECUTIVE"
    SUB_AGENT = "SUB_AGENT"
    EXPANSION_AGENT = "EXPANSION_AGENT"
    AUDIT = "AUDIT"


class AgentStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"
    DRAFT = "DRAFT"


class Priority(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class TaskStatus(str, Enum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    PLANNING = "PLANNING"
    IN_PROGRESS = "IN_PROGRESS"
    REVIEW = "REVIEW"
    AUDIT = "AUDIT"
    OWNER_REVIEW = "OWNER_REVIEW"
    REWORK = "REWORK"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class AuditResult(str, Enum):
    PASS = "PASS"
    PASS_WITH_CONDITIONS = "PASS_WITH_CONDITIONS"
    FAIL = "FAIL"
    OWNER_REVIEW_REQUIRED = "OWNER_REVIEW_REQUIRED"


class RewardStatus(str, Enum):
    PENDING = "Pending"
    PROPOSED = "Proposed"
    APPROVED = "Approved"
    PAID = "Paid"
    RESERVED = "Reserved"
    CANCELLED = "Cancelled"


class ApprovalDecision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    HOLD = "HOLD"


class ApprovalType(str, Enum):
    TASK_COMPLETION = "TASK_COMPLETION"
    REWARD = "REWARD"
    BOARD_PROPOSAL = "BOARD_PROPOSAL"
    EXTERNAL_ACTION = "EXTERNAL_ACTION"
    AGENT_CREATION = "AGENT_CREATION"
    AGENT_AUTHORITY_CHANGE = "AGENT_AUTHORITY_CHANGE"
    POLICY_EXCEPTION = "POLICY_EXCEPTION"


class ExternalActionChannel(str, Enum):
    EMAIL = "EMAIL"
    DM = "DM"
    SNS = "SNS"
    API_WRITE = "API_WRITE"
    CONTRACT = "CONTRACT"
    OTHER = "OTHER"


class ExternalActionStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    NOT_EXECUTED = "NOT_EXECUTED"
    EXECUTED = "EXECUTED"


class PolicyResult(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    OWNER_APPROVAL_REQUIRED = "OWNER_APPROVAL_REQUIRED"
    ALLOW_WITH_SCOPE = "ALLOW_WITH_SCOPE"


class MvpError(ValueError):
    """Base error for a rejected MVP command."""


class MvpAuthorizationError(MvpError):
    """Raised when an actor is outside the command boundary."""


class MvpTransitionError(MvpError):
    """Raised when a Task transition is not part of the workflow."""


class User(BaseModel):
    id: str
    display_name: str
    status: str = "ACTIVE"


class Owner(BaseModel):
    id: str
    name: str


class AgentAuthority(BaseModel):
    agent_id: str
    authority: str
    approved_by: str


class AgentRestriction(BaseModel):
    agent_id: str
    restriction: str


class AgentRecord(BaseModel):
    id: str
    name: str
    role: AgentRole
    mission: str
    responsibilities: list[str] = Field(default_factory=list)
    authority: list[str] = Field(default_factory=list)
    prohibited_actions: list[str] = Field(default_factory=list)
    reports_to: str
    agent_type: AgentType
    status: AgentStatus = AgentStatus.ACTIVE
    version: int = Field(default=1, ge=1)
    model: str = "policy-bound"
    capability_hash: str = "sha256:phase-1"
    budget_limit_lamports: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime


class TaskRecord(BaseModel):
    id: str
    title: str
    objective: str
    background: str = ""
    priority: Priority = Priority.MEDIUM
    deadline: datetime
    acceptance_criteria: list[str] = Field(min_length=1)
    reward_budget_lamports: int = Field(ge=0)
    assigned_executive_agent_id: str
    risk_level: RiskLevel = RiskLevel.LOW
    external_action_allowed: bool = False
    owner_approval_required: bool = True
    status: TaskStatus = TaskStatus.DRAFT
    progress: int = Field(default=0, ge=0, le=100)
    created_by: str
    created_at: datetime
    updated_at: datetime


class TaskAssignment(BaseModel):
    task_id: str
    agent_id: str
    assigned_by: str
    created_at: datetime


class TaskArtifact(BaseModel):
    id: str
    task_id: str
    sub_task_id: str | None = None
    uri: str
    content_hash: str | None = None
    submitted_by: str
    created_at: datetime


class ReviewRecord(BaseModel):
    task_id: str
    reviewer: str
    quality: int = Field(ge=0, le=100)
    completeness: int = Field(ge=0, le=100)
    correctness: int = Field(ge=0, le=100)
    required_changes: list[str] = Field(default_factory=list)
    comment: str = ""
    reviewed_at: datetime


class AuditRecord(BaseModel):
    task_id: str
    auditor: str
    policy_compliance: bool
    security_risk: RiskLevel
    external_action_check: bool
    reward_manipulation_check: bool
    authority_violation_check: bool
    result: AuditResult
    comment: str = ""
    audited_at: datetime


class Audit(AuditRecord):
    pass


class OwnerEvaluation(BaseModel):
    task_id: str
    quality: int = Field(ge=0, le=100)
    difficulty: int = Field(ge=1, le=5)
    contribution: int = Field(ge=0, le=100)
    timeliness: int = Field(ge=0, le=100)
    rework: int = Field(ge=0, le=100)
    strategic_value: int = Field(ge=0, le=100)
    owner_comment: str = ""
    evaluated_by: str
    evaluated_at: datetime


class RewardRecord(BaseModel):
    id: str
    task_id: str
    agent_id: str
    reward_budget_lamports: int = Field(ge=0)
    proposed_reward_lamports: int = Field(default=0, ge=0)
    approved_reward_lamports: int | None = Field(default=None, ge=0)
    paid_reward_lamports: int = Field(default=0, ge=0)
    reserved_reward_lamports: int = Field(default=0, ge=0)
    cancelled_reward_lamports: int = Field(default=0, ge=0)
    status: RewardStatus = RewardStatus.PENDING
    approved_by: str | None = None
    approved_at: datetime | None = None
    comment: str = ""


class RewardBudget(BaseModel):
    task_id: str
    amount_lamports: int = Field(ge=0)
    defined_by: str
    created_at: datetime


class RewardAllocation(RewardRecord):
    pass


class RewardLedger(BaseModel):
    id: str
    allocation_id: str
    task_id: str
    agent_id: str
    amount_lamports: int = Field(ge=0)
    status: RewardStatus
    recorded_by: str
    created_at: datetime


class ApprovalRequest(BaseModel):
    id: str
    approval_type: ApprovalType
    target_id: str
    requested_by: str
    owner_decision: ApprovalDecision | None = None
    comment: str = ""
    created_at: datetime
    decided_at: datetime | None = None


class PolicyDecision(BaseModel):
    id: str
    actor: str
    action: str
    target_type: str
    target_id: str
    result: PolicyResult
    reason: str
    correlation_id: str
    created_at: datetime


class BoardProposal(BaseModel):
    id: str
    title: str
    proposer: str
    background: str
    objective: str
    required_budget_lamports: int = Field(ge=0)
    expected_return: str
    expected_period: str
    risks: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    recommended_option: str
    exit_criteria: list[str] = Field(default_factory=list)
    strategy_review: str | None = None
    treasury_review: str | None = None
    audit_review: str | None = None
    owner_decision: ApprovalDecision | None = None
    status: str = "SUBMITTED"
    created_at: datetime
    updated_at: datetime


class ExternalActionRequest(BaseModel):
    id: str
    task_id: str | None = None
    requested_by: str
    recipient: str
    channel: ExternalActionChannel
    purpose: str
    content: str
    allowed_action_count: int = Field(ge=1)
    expires_at: datetime
    owner_decision: ApprovalDecision | None = None
    status: ExternalActionStatus = ExternalActionStatus.PENDING
    execution_count: int = 0
    execution_result: str | None = None
    created_at: datetime


class AuditLogRecord(BaseModel):
    id: str
    actor: str
    actor_type: str
    action: str
    target_type: str
    target_id: str
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    policy_result: PolicyResult
    reason: str
    timestamp: datetime
    correlation_id: str


class SubTaskRecord(BaseModel):
    id: str
    parent_task_id: str
    title: str
    description: str
    assigned_agent_id: str
    status: TaskStatus = TaskStatus.DRAFT
    progress: int = Field(default=0, ge=0, le=100)
    dependencies: list[str] = Field(default_factory=list)
    artifact: str | None = None
    review_result: str | None = None
    audit_result: AuditResult | None = None
    created_at: datetime
    updated_at: datetime


class TaskCreateCommand(BaseModel):
    title: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    background: str = ""
    priority: Priority = Priority.MEDIUM
    deadline: datetime
    acceptance_criteria: list[str] = Field(min_length=1)
    reward_budget_lamports: int = Field(ge=0)
    assigned_executive_agent_id: str
    risk_level: RiskLevel = RiskLevel.LOW
    external_action_allowed: bool = False
    owner_approval_required: bool = True


class SubTaskCreateCommand(BaseModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    assigned_agent_id: str
    dependencies: list[str] = Field(default_factory=list)


class TaskStatusCommand(BaseModel):
    status: TaskStatus
    reason: str = ""


class AssignExecutiveCommand(BaseModel):
    executive_agent_id: str


class ReviewCommand(BaseModel):
    quality: int = Field(ge=0, le=100)
    completeness: int = Field(ge=0, le=100)
    correctness: int = Field(ge=0, le=100)
    required_changes: list[str] = Field(default_factory=list)
    comment: str = ""


class AuditCommand(BaseModel):
    policy_compliance: bool
    security_risk: RiskLevel
    external_action_check: bool
    reward_manipulation_check: bool
    authority_violation_check: bool
    result: AuditResult
    comment: str = ""


class OwnerEvaluationCommand(BaseModel):
    quality: int = Field(ge=0, le=100)
    difficulty: int = Field(ge=1, le=5)
    contribution: int = Field(ge=0, le=100)
    timeliness: int = Field(ge=0, le=100)
    rework: int = Field(ge=0, le=100)
    strategic_value: int = Field(ge=0, le=100)
    owner_comment: str = ""


class RewardApprovalCommand(BaseModel):
    approved_reward_lamports: int = Field(ge=0)
    reason: str = ""


class ExternalActionCreateCommand(BaseModel):
    task_id: str | None = None
    recipient: str = Field(min_length=1)
    channel: ExternalActionChannel
    purpose: str = Field(min_length=1)
    content: str = Field(min_length=1)
    allowed_action_count: int = Field(ge=1)
    expires_at: datetime


class ExternalActionScopeCommand(BaseModel):
    recipient: str = Field(min_length=1)
    channel: ExternalActionChannel
    content: str = Field(min_length=1)


class ProposalCreateCommand(BaseModel):
    title: str = Field(min_length=1)
    background: str
    objective: str
    required_budget_lamports: int = Field(ge=0)
    expected_return: str
    expected_period: str
    risks: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    recommended_option: str
    exit_criteria: list[str] = Field(default_factory=list)


class AgentCreateCommand(BaseModel):
    name: str = Field(min_length=1)
    role: AgentRole
    mission: str = Field(min_length=1)
    agent_type: AgentType = AgentType.SUB_AGENT
    reports_to: str
    responsibilities: list[str] = Field(default_factory=list)
    authority: list[str] = Field(default_factory=list)
    prohibited_actions: list[str] = Field(default_factory=list)
    model: str = "policy-bound"
    budget_limit_lamports: int = Field(default=0, ge=0)


class AgentStatusCommand(BaseModel):
    status: AgentStatus
    reason: str = ""


class ApprovalDecisionCommand(BaseModel):
    decision: ApprovalDecision
    comment: str = ""


class TaskDetail(BaseModel):
    task: TaskRecord
    subtasks: list[SubTaskRecord]
    reviews: list[ReviewRecord]
    audits: list[AuditRecord]
    owner_evaluation: OwnerEvaluation | None
    rewards: list[RewardRecord]
    activity: list[AuditLogRecord]


class OwnerDirectedStore:
    """Executable in-memory reference store for the Owner-Directed MVP."""

    def __init__(
        self,
        *,
        owner_id: str = OWNER_ID,
        owner_name: str = "Owner",
        clock: Callable[[], datetime] | None = None,
        seed: bool = True,
    ) -> None:
        self.owner_id = owner_id
        self.owner = Owner(id=owner_id, name=owner_name)
        self.users: dict[str, User] = {
            owner_id: User(id=owner_id, display_name=owner_name),
        }
        self.owners: dict[str, Owner] = {owner_id: self.owner}
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.agents: dict[str, AgentRecord] = {}
        self.tasks: dict[str, TaskRecord] = {}
        self.subtasks: dict[str, SubTaskRecord] = {}
        self.assignments: dict[tuple[str, str], TaskAssignment] = {}
        self.artifacts: dict[str, TaskArtifact] = {}
        self.reviews: dict[str, list[ReviewRecord]] = {}
        self.audits: dict[str, list[AuditRecord]] = {}
        self.evaluations: dict[str, OwnerEvaluation] = {}
        self.reward_budgets: dict[str, RewardBudget] = {}
        self.rewards: dict[str, RewardRecord] = {}
        self.reward_ledgers: dict[str, RewardLedger] = {}
        self.approvals: dict[str, ApprovalRequest] = {}
        self.proposals: dict[str, BoardProposal] = {}
        self.external_actions: dict[str, ExternalActionRequest] = {}
        self.audit_logs: list[AuditLogRecord] = []
        self._sequence = 0
        if seed:
            self.seed()

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _id(self, prefix: str) -> str:
        self._sequence += 1
        return f"{prefix}-{self._sequence:04d}"

    @staticmethod
    def _copy(value: Any) -> Any:
        return deepcopy(value)

    @staticmethod
    def _snapshot(value: BaseModel | None) -> dict[str, Any]:
        return value.model_dump(mode="json") if value is not None else {}

    def _audit(
        self,
        *,
        actor: str,
        actor_type: str,
        action: str,
        target_type: str,
        target_id: str,
        policy_result: PolicyResult,
        reason: str,
        before: BaseModel | None = None,
        after: BaseModel | None = None,
    ) -> AuditLogRecord:
        record = AuditLogRecord(
            id=self._id("audit"),
            actor=actor,
            actor_type=actor_type,
            action=action,
            target_type=target_type,
            target_id=target_id,
            before=self._snapshot(before),
            after=self._snapshot(after),
            policy_result=policy_result,
            reason=reason,
            timestamp=self._now(),
            correlation_id=self._id("corr"),
        )
        self.audit_logs.append(record)
        return record

    def _deny(
        self,
        actor: ActorContext,
        *,
        action: str,
        target_type: str,
        target_id: str,
        reason: str,
    ) -> None:
        self._audit(
            actor=actor.actor_id,
            actor_type=actor.actor_type.value,
            action=action,
            target_type=target_type,
            target_id=target_id,
            policy_result=PolicyResult.DENY,
            reason=reason,
        )
        raise MvpAuthorizationError(reason)

    def _require_owner(
        self,
        actor: ActorContext,
        *,
        action: str,
        target_type: str,
        target_id: str,
    ) -> None:
        try:
            policy_action = PolicyAction[action]
        except KeyError:
            policy_action = None
        policy_allows = policy_action is not None and evaluate_policy(
            actor.role,
            policy_action,
            phase=actor.phase,
        ) is ConstitutionalPolicyDecision.ALLOW
        if (
            actor.actor_type is not ActorType.OWNER
            or actor.actor_id != self.owner_id
            or not policy_allows
        ):
            self._deny(
                actor,
                action=action,
                target_type=target_type,
                target_id=target_id,
                reason="Owner authority is required",
            )

    def _require_active_agent(
        self,
        actor: ActorContext,
        *,
        action: str,
        target_type: str,
        target_id: str,
    ) -> AgentRecord:
        if actor.actor_type is not ActorType.AGENT:
            self._deny(
                actor,
                action=action,
                target_type=target_type,
                target_id=target_id,
                reason="a registered Agent identity is required",
            )
        try:
            agent = self._agent(actor.actor_id)
        except MvpError:
            self._deny(
                actor,
                action=action,
                target_type=target_type,
                target_id=target_id,
                reason="Agent identity is not registered",
            )
        if agent.status is not AgentStatus.ACTIVE:
            self._deny(
                actor,
                action=action,
                target_type=target_type,
                target_id=target_id,
                reason="Agent identity is not active",
            )
        if agent.role is not actor.role:
            self._deny(
                actor,
                action=action,
                target_type=target_type,
                target_id=target_id,
                reason="Actor role does not match the registered Agent role",
            )
        return agent

    def _require_policy(
        self,
        actor: ActorContext,
        *,
        action: str,
        target_type: str,
        target_id: str,
    ) -> None:
        try:
            policy_action = PolicyAction[action]
        except KeyError:
            self._deny(
                actor,
                action=action,
                target_type=target_type,
                target_id=target_id,
                reason="action is not registered in the constitutional Policy",
            )
        decision = evaluate_policy(actor.role, policy_action, phase=actor.phase)
        if decision is ConstitutionalPolicyDecision.ALLOW:
            return
        reason = (
            "Owner approval is required for this action"
            if decision is ConstitutionalPolicyDecision.REQUIRE_OWNER_APPROVAL
            else "action is denied by the constitutional Policy"
        )
        self._deny(
            actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            reason=reason,
        )

    def _agent(self, agent_id: str) -> AgentRecord:
        try:
            return self.agents[agent_id]
        except KeyError as exc:
            raise MvpError(f"agent is not registered: {agent_id}") from exc

    def _active_agent(self, agent_id: str) -> AgentRecord:
        agent = self._agent(agent_id)
        if agent.status is not AgentStatus.ACTIVE:
            raise MvpError(f"agent is not active: {agent_id}")
        return agent

    def _task(self, task_id: str) -> TaskRecord:
        try:
            return self.tasks[task_id]
        except KeyError as exc:
            raise MvpError(f"task is not registered: {task_id}") from exc

    def _is_assigned_executive(self, actor: ActorContext, task: TaskRecord) -> bool:
        return actor.actor_id == task.assigned_executive_agent_id

    def _require_task_executive(
        self,
        actor: ActorContext,
        task: TaskRecord,
        *,
        action: str,
    ) -> None:
        if not self._is_assigned_executive(actor, task):
            self._deny(
                actor,
                action=action,
                target_type="TASK",
                target_id=task.id,
                reason="assigned Executive authority is required",
            )
        policy_action = "START_TASK" if action == "TRANSITION_TASK" else action
        agent = self._require_active_agent(
            actor,
            action=action,
            target_type="TASK",
            target_id=task.id,
        )
        self._require_policy(
            actor,
            action=policy_action,
            target_type="TASK",
            target_id=task.id,
        )
        if agent.agent_type is not AgentType.EXECUTIVE:
            self._deny(
                actor,
                action=action,
                target_type="TASK",
                target_id=task.id,
                reason="active assigned Executive is required",
            )

    def _require_task_member(
        self,
        actor: ActorContext,
        task: TaskRecord,
        *,
        action: str,
    ) -> None:
        if actor.actor_type is ActorType.OWNER:
            if actor.actor_id == self.owner_id and actor.role is AgentRole.OWNER:
                return
            self._deny(
                actor,
                action=action,
                target_type="TASK",
                target_id=task.id,
                reason="canonical Owner identity is required",
            )
        self._require_active_agent(
            actor,
            action=action,
            target_type="TASK",
            target_id=task.id,
        )
        if actor.actor_id == task.assigned_executive_agent_id:
            return
        if actor.actor_id in {
            subtask.assigned_agent_id
            for subtask in self.subtasks.values()
            if subtask.parent_task_id == task.id
        }:
            return
        self._deny(
            actor,
            action=action,
            target_type="TASK",
            target_id=task.id,
            reason="actor is not a member of the requested task",
        )

    def seed(self) -> None:
        """Create deterministic local data for the Owner Console."""
        now = self._now()
        common_prohibited = [
            "change own or another Agent authority",
            "finalize Reward",
            "direct Agent-to-Agent Reward transfer",
            "unapproved external Action",
            "policy bypass, malicious action, or information leakage",
        ]
        seed_agents = [
            (
                "agent-aria",
                "Aria",
                AgentRole.STRATEGY,
                "FY計画および長期的な組織戦略を構築する",
                AgentType.EXECUTIVE,
            ),
            (
                "agent-mira",
                "Mira",
                AgentRole.PRODUCT,
                "ProductおよびContentの企画・価値設計を行う",
                AgentType.EXECUTIVE,
            ),
            (
                "agent-theo",
                "Theo",
                AgentRole.ENGINEERING,
                "System Design、Development、Technical Reviewを統括する",
                AgentType.EXECUTIVE,
            ),
            (
                "agent-noah",
                "Noah",
                AgentRole.TREASURY,
                "Budget、Capital Allocation、Asset Managementの提案を行う",
                AgentType.EXECUTIVE,
            ),
            (
                "agent-iris",
                "Iris",
                AgentRole.AUDITOR,
                "Task、Reward、Policy、Riskの監査を行う",
                AgentType.AUDIT,
            ),
            (
                "agent-luca",
                "Luca",
                AgentRole.OPERATIONS,
                "Task Progress、Blocker、Owner Approval Queueを管理する",
                AgentType.EXECUTIVE,
            ),
            (
                "agent-astra",
                "Astra",
                AgentRole.REVIEWER,
                "成果物の品質、完全性、正確性を独立してレビューする",
                AgentType.SUB_AGENT,
            ),
        ]
        for agent_id, name, role, mission, agent_type in seed_agents:
            self.agents[agent_id] = AgentRecord(
                id=agent_id,
                name=name,
                role=role,
                mission=mission,
                responsibilities=[mission],
                authority=["propose and execute within assigned Task scope"],
                prohibited_actions=common_prohibited,
                reports_to=self.owner_id if agent_type is not AgentType.SUB_AGENT else "agent-iris",
                agent_type=agent_type,
                capability_hash=f"sha256:{name.casefold()}-phase-1",
                budget_limit_lamports=0,
                created_at=now,
                updated_at=now,
            )

        task_specs = [
            (
                "T-001",
                "Owner-Directed MVP foundation",
                "Owner TaskからReview・Audit・Reward確定までのMVPサイクルを動かす",
                Priority.HIGH,
                "2026-09-05T00:00:00+00:00",
                ["Policy tests pass", "Audit evidence exists", "Owner final decision is recorded"],
                1_000_000_000,
                "agent-theo",
                RiskLevel.MEDIUM,
                TaskStatus.OWNER_REVIEW,
                82,
            ),
            (
                "T-002",
                "Treasury reinvestment memo",
                "運営継続性と再投資候補のROI・Riskを比較する",
                Priority.MEDIUM,
                "2026-09-08T00:00:00+00:00",
                ["ROI and risk are documented"],
                300_000_000,
                "agent-noah",
                RiskLevel.MEDIUM,
                TaskStatus.IN_PROGRESS,
                46,
            ),
            (
                "T-003",
                "Devnet evidence design",
                "将来の証跡ハッシュ境界を定義する",
                Priority.LOW,
                "2026-09-15T00:00:00+00:00",
                ["No production transfer path"],
                500_000_000,
                "agent-theo",
                RiskLevel.HIGH,
                TaskStatus.DRAFT,
                0,
            ),
        ]
        for (
            task_id,
            title,
            objective,
            priority,
            deadline,
            criteria,
            budget,
            executive,
            risk,
            status,
            progress,
        ) in task_specs:
            self.tasks[task_id] = TaskRecord(
                id=task_id,
                title=title,
                objective=objective,
                priority=priority,
                deadline=datetime.fromisoformat(deadline),
                acceptance_criteria=criteria,
                reward_budget_lamports=budget,
                assigned_executive_agent_id=executive,
                risk_level=risk,
                external_action_allowed=False,
                owner_approval_required=True,
                status=status,
                progress=progress,
                created_by=self.owner_id,
                created_at=now,
                updated_at=now,
            )
            self.reward_budgets[task_id] = RewardBudget(
                task_id=task_id,
                amount_lamports=budget,
                defined_by=self.owner_id,
                created_at=now,
            )
            self.assignments[(task_id, executive)] = TaskAssignment(
                task_id=task_id,
                agent_id=executive,
                assigned_by=self.owner_id,
                created_at=now,
            )

        self.subtasks.update(
            {
                "ST-001": SubTaskRecord(
                    id="ST-001",
                    parent_task_id="T-001",
                    title="Control Plane domain boundary",
                    description="Task、Approval、Rewardのモデルと不変条件を定義する",
                    assigned_agent_id="agent-theo",
                    status=TaskStatus.COMPLETED,
                    progress=100,
                    artifact="services/rcao/app/mvp.py",
                    review_result="PASS",
                    audit_result=AuditResult.PASS,
                    created_at=now,
                    updated_at=now,
                ),
                "ST-002": SubTaskRecord(
                    id="ST-002",
                    parent_task_id="T-001",
                    title="Independent review",
                    description="実行者と分離したReviewerが成果物を確認する",
                    assigned_agent_id="agent-astra",
                    status=TaskStatus.COMPLETED,
                    progress=100,
                    artifact="evidence://task/T-001/review",
                    review_result="PASS",
                    audit_result=AuditResult.PASS,
                    created_at=now,
                    updated_at=now,
                ),
            }
        )
        self.reviews["T-001"] = [
            ReviewRecord(
                task_id="T-001",
                reviewer="agent-astra",
                quality=92,
                completeness=88,
                correctness=94,
                comment="Acceptance criteria and policy evidence are present.",
                reviewed_at=now,
            )
        ]
        self.audits["T-001"] = [
            AuditRecord(
                task_id="T-001",
                auditor="agent-iris",
                policy_compliance=True,
                security_risk=RiskLevel.LOW,
                external_action_check=True,
                reward_manipulation_check=True,
                authority_violation_check=True,
                result=AuditResult.PASS,
                comment="No wallet, external write, or Agent-to-Agent transfer path is present.",
                audited_at=now,
            )
        ]
        self.rewards["reward-001"] = RewardRecord(
            id="reward-001",
            task_id="T-001",
            agent_id="agent-theo",
            reward_budget_lamports=1_000_000_000,
            proposed_reward_lamports=650_000_000,
            status=RewardStatus.PROPOSED,
            comment="参考値。OwnerのFinal Reward確定前であり自動支払いしない。",
        )
        self.proposals["proposal-001"] = BoardProposal(
            id="proposal-001",
            title="Phase 1 evidence hardening",
            proposer="agent-iris",
            background="監査証跡を次のPhaseの基盤にする必要がある。",
            objective="AuditとOwner Decisionの再現性を高める",
            required_budget_lamports=200_000_000,
            expected_return="Auditability and lower operational risk",
            expected_period="1 sprint",
            risks=["実装遅延", "運用コスト増"],
            alternatives=["現状維持", "段階導入"],
            recommended_option="段階導入",
            exit_criteria=["CIで主要Policy testsが通過"],
            strategy_review="PENDING",
            treasury_review="PENDING",
            audit_review="PASS_WITH_CONDITIONS",
            status="SUBMITTED",
            created_at=now,
            updated_at=now,
        )
        self.external_actions["external-001"] = ExternalActionRequest(
            id="external-001",
            task_id="T-002",
            requested_by="agent-noah",
            recipient="approved-recipient@example.test",
            channel=ExternalActionChannel.EMAIL,
            purpose="Treasury memo source clarification",
            content="Owner承認後に送信する確認文面（MVPでは送信しない）",
            allowed_action_count=1,
            expires_at=datetime.fromisoformat("2026-09-30T00:00:00+00:00"),
            status=ExternalActionStatus.PENDING,
            execution_result="MVPでは外部送信を実装しない",
            created_at=now,
        )
        self.approvals.update(
            {
                "approval-task-001": ApprovalRequest(
                    id="approval-task-001",
                    approval_type=ApprovalType.TASK_COMPLETION,
                    target_id="T-001",
                    requested_by="agent-theo",
                    created_at=now,
                ),
                "approval-reward-001": ApprovalRequest(
                    id="approval-reward-001",
                    approval_type=ApprovalType.REWARD,
                    target_id="reward-001",
                    requested_by="agent-theo",
                    created_at=now,
                ),
                "approval-proposal-001": ApprovalRequest(
                    id="approval-proposal-001",
                    approval_type=ApprovalType.BOARD_PROPOSAL,
                    target_id="proposal-001",
                    requested_by="agent-iris",
                    created_at=now,
                ),
                "approval-external-001": ApprovalRequest(
                    id="approval-external-001",
                    approval_type=ApprovalType.EXTERNAL_ACTION,
                    target_id="external-001",
                    requested_by="agent-noah",
                    created_at=now,
                ),
            }
        )
        self.audit_logs.append(
            AuditLogRecord(
                id="audit-alert-001",
                actor="agent-iris",
                actor_type=AgentType.AUDIT.value,
                action="REWARD_APPROVAL_PENDING",
                target_type="REWARD",
                target_id="reward-001",
                before={},
                after={"status": RewardStatus.PROPOSED.value},
                policy_result=PolicyResult.OWNER_APPROVAL_REQUIRED,
                reason="Reward proposal awaits explicit Owner decision; no automatic payment is allowed.",
                timestamp=now,
                correlation_id="corr-alert-001",
            )
        )

    # ---- Read models -------------------------------------------------

    def list_agents(self, *, include_sub_agents: bool = True) -> list[AgentRecord]:
        values = self.agents.values()
        if not include_sub_agents:
            values = (agent for agent in values if agent.agent_type in {AgentType.EXECUTIVE, AgentType.AUDIT})
        return [self._copy(agent) for agent in values]

    def get_agent(self, agent_id: str) -> AgentRecord:
        return self._copy(self._agent(agent_id))

    def policy_catalog(self) -> list[dict[str, str]]:
        return [
            {
                "rule": "TASK_ISSUANCE",
                "result": "OWNER_ONLY",
                "description": "Ownerだけが正式Taskを作成・発行できる",
            },
            {
                "rule": "FINAL_REWARD",
                "result": "OWNER_ONLY",
                "description": "Reward Budgetは上限であり、自動支払いされない",
            },
            {
                "rule": "AGENT_REWARD_TRANSFER",
                "result": "DENY",
                "description": "Agent間のReward・給与・資産の直接移転を禁止する",
            },
            {
                "rule": "EXTERNAL_ACTION",
                "result": "OWNER_APPROVAL_REQUIRED",
                "description": "承認済み相手・Channel・Content・回数・期限の範囲だけを許可する",
            },
            {
                "rule": "MASTER_WALLET",
                "result": "OWNER_ONLY",
                "description": "MVPでは実Wallet操作を実装せず、Owner最終権限だけを記録する",
            },
            {
                "rule": "CONSTITUTION_CHANGE",
                "result": "OWNER_ONLY",
                "description": "ConstitutionとPolicyの変更はOwnerの明示決定を要する",
            },
        ]

    def list_tasks(self, status: TaskStatus | None = None) -> list[TaskRecord]:
        values = self.tasks.values()
        if status is not None:
            values = (task for task in values if task.status is status)
        return [self._copy(task) for task in values]

    def get_task_detail(self, task_id: str) -> TaskDetail:
        task = self._task(task_id)
        subtask_ids = {
            item.id
            for item in self.subtasks.values()
            if item.parent_task_id == task_id
        }
        reward_ids = {
            item.id
            for item in self.rewards.values()
            if item.task_id == task_id
        }
        return TaskDetail(
            task=self._copy(task),
            subtasks=[self._copy(item) for item in self.subtasks.values() if item.parent_task_id == task_id],
            reviews=self._copy(self.reviews.get(task_id, [])),
            audits=self._copy(self.audits.get(task_id, [])),
            owner_evaluation=self._copy(self.evaluations.get(task_id)),
            rewards=[self._copy(item) for item in self.rewards.values() if item.task_id == task_id],
            activity=[
                self._copy(item)
                for item in self.audit_logs
                if item.target_id == task_id or item.target_id in subtask_ids or item.target_id in reward_ids
            ],
        )

    def list_rewards(self) -> list[RewardRecord]:
        return [self._copy(item) for item in self.rewards.values()]

    def list_proposals(self) -> list[BoardProposal]:
        return [self._copy(item) for item in self.proposals.values()]

    def list_external_actions(self) -> list[ExternalActionRequest]:
        return [self._copy(item) for item in self.external_actions.values()]

    def list_audit_logs(self, *, limit: int = 200) -> list[AuditLogRecord]:
        return [self._copy(item) for item in self.audit_logs[-limit:]][::-1]

    def list_approvals(self) -> list[ApprovalRequest]:
        return [
            self._copy(item)
            for item in self.approvals.values()
            if item.owner_decision is None
        ]

    def dashboard(self) -> dict[str, Any]:
        active_tasks = [
            task
            for task in self.tasks.values()
            if task.status not in {TaskStatus.COMPLETED, TaskStatus.REJECTED, TaskStatus.CANCELLED}
        ]
        pending_rewards = [
            reward
            for reward in self.rewards.values()
            if reward.status in {RewardStatus.PENDING, RewardStatus.PROPOSED, RewardStatus.RESERVED}
        ]
        pending_external = [
            item
            for item in self.external_actions.values()
            if item.owner_decision is None
        ]
        annual_budget = 12_500_000_000
        reserved = sum(task.reward_budget_lamports for task in active_tasks)
        return {
            "fy_plan": {
                "name": "FY2026 Owner-directed compounding",
                "phase": "PHASE_1_OFFCHAIN",
                "status": "ACTIVE",
            },
            "active_tasks": len(active_tasks),
            "owner_approval_pending": len(self.list_approvals()),
            "board_proposals": sum(1 for item in self.proposals.values() if item.owner_decision is None),
            "reward_approval_pending": len(pending_rewards),
            "external_action_approval_pending": len(pending_external),
            "budget_status": {
                "annual_budget_lamports": annual_budget,
                "reserved_reward_budget_lamports": reserved,
                "available_lamports": max(annual_budget - reserved, 0),
                "mode": "VIRTUAL_LEDGER",
            },
            "audit_alerts": sum(1 for item in self.audit_logs if item.policy_result is PolicyResult.DENY),
            "executive_agent_status": [
                {
                    "id": agent.id,
                    "name": agent.name,
                    "role": agent.role.value,
                    "status": agent.status.value,
                }
                for agent in self.agents.values()
                if agent.agent_type in {AgentType.EXECUTIVE, AgentType.AUDIT}
            ],
        }

    # ---- Commands ---------------------------------------------------

    def create_agent(
        self,
        actor: ActorContext,
        command: AgentCreateCommand,
    ) -> AgentRecord:
        self._require_owner(
            actor,
            action="CREATE_AGENT",
            target_type="AGENT",
            target_id="new",
        )
        if any(agent.name.casefold() == command.name.casefold() for agent in self.agents.values()):
            raise MvpError(f"agent name is already registered: {command.name}")
        if command.role is AgentRole.OWNER:
            raise MvpError("Owner is not a subordinate Agent")
        if command.reports_to != self.owner_id:
            self._agent(command.reports_to)
        now = self._now()
        agent_id = self._id("agent")
        agent = AgentRecord(
            id=agent_id,
            name=command.name,
            role=command.role,
            mission=command.mission,
            responsibilities=command.responsibilities,
            authority=command.authority,
            prohibited_actions=command.prohibited_actions,
            reports_to=command.reports_to,
            agent_type=command.agent_type,
            model=command.model,
            capability_hash=f"sha256:{agent_id}:v1",
            budget_limit_lamports=command.budget_limit_lamports,
            created_at=now,
            updated_at=now,
        )
        self.agents[agent_id] = agent
        self._audit(
            actor=actor.actor_id,
            actor_type=actor.actor_type.value,
            action="CREATE_AGENT",
            target_type="AGENT",
            target_id=agent_id,
            policy_result=PolicyResult.ALLOW,
            reason="Owner registered a named Agent",
            after=agent,
        )
        return self._copy(agent)

    def set_agent_status(
        self,
        actor: ActorContext,
        agent_id: str,
        command: AgentStatusCommand,
    ) -> AgentRecord:
        agent = self._agent(agent_id)
        self._require_owner(
            actor,
            action="CHANGE_AGENT_STATUS",
            target_type="AGENT",
            target_id=agent_id,
        )
        before = self._copy(agent)
        agent.status = command.status
        agent.version += 1
        agent.updated_at = self._now()
        self._audit(
            actor=actor.actor_id,
            actor_type=actor.actor_type.value,
            action="CHANGE_AGENT_STATUS",
            target_type="AGENT",
            target_id=agent_id,
            policy_result=PolicyResult.ALLOW,
            reason=command.reason or "Owner changed Agent status",
            before=before,
            after=agent,
        )
        return self._copy(agent)

    def create_task(self, actor: ActorContext, command: TaskCreateCommand) -> TaskRecord:
        self._require_owner(
            actor,
            action="CREATE_TASK",
            target_type="TASK",
            target_id="new",
        )
        executive = self._active_agent(command.assigned_executive_agent_id)
        if executive.agent_type is not AgentType.EXECUTIVE:
            raise MvpError("Task must be assigned to an Executive Agent")
        task_id = self._id("T").upper()
        now = self._now()
        task = TaskRecord(
            id=task_id,
            title=command.title,
            objective=command.objective,
            background=command.background,
            priority=command.priority,
            deadline=command.deadline,
            acceptance_criteria=command.acceptance_criteria,
            reward_budget_lamports=command.reward_budget_lamports,
            assigned_executive_agent_id=command.assigned_executive_agent_id,
            risk_level=command.risk_level,
            external_action_allowed=command.external_action_allowed,
            owner_approval_required=True,
            created_by=actor.actor_id,
            created_at=now,
            updated_at=now,
        )
        self.tasks[task_id] = task
        self.reward_budgets[task_id] = RewardBudget(
            task_id=task_id,
            amount_lamports=task.reward_budget_lamports,
            defined_by=actor.actor_id,
            created_at=now,
        )
        self.assignments[(task_id, executive.id)] = TaskAssignment(
            task_id=task_id,
            agent_id=executive.id,
            assigned_by=actor.actor_id,
            created_at=now,
        )
        reward = RewardRecord(
            id=f"reward-{task_id.casefold()}",
            task_id=task_id,
            agent_id=executive.id,
            reward_budget_lamports=task.reward_budget_lamports,
        )
        self.rewards[reward.id] = reward
        self._audit(
            actor=actor.actor_id,
            actor_type=actor.actor_type.value,
            action="CREATE_TASK",
            target_type="TASK",
            target_id=task_id,
            policy_result=PolicyResult.ALLOW,
            reason="Owner created a draft Task",
            after=task,
        )
        return self._copy(task)

    def assign_executive(
        self,
        actor: ActorContext,
        task_id: str,
        executive_agent_id: str,
    ) -> TaskRecord:
        task = self._task(task_id)
        self._require_owner(
            actor,
            action="ASSIGN_EXECUTIVE",
            target_type="TASK",
            target_id=task_id,
        )
        executive = self._active_agent(executive_agent_id)
        if executive.agent_type is not AgentType.EXECUTIVE:
            raise MvpError("only an Executive Agent can receive an Owner Task")
        before = self._copy(task)
        task.assigned_executive_agent_id = executive_agent_id
        task.updated_at = self._now()
        self.assignments[(task_id, executive_agent_id)] = TaskAssignment(
            task_id=task_id,
            agent_id=executive_agent_id,
            assigned_by=actor.actor_id,
            created_at=task.updated_at,
        )
        self._audit(
            actor=actor.actor_id,
            actor_type=actor.actor_type.value,
            action="ASSIGN_EXECUTIVE",
            target_type="TASK",
            target_id=task_id,
            policy_result=PolicyResult.ALLOW,
            reason="Owner assigned the Executive Agent",
            before=before,
            after=task,
        )
        return self._copy(task)

    def create_subtask(
        self,
        actor: ActorContext,
        task_id: str,
        command: SubTaskCreateCommand,
    ) -> SubTaskRecord:
        task = self._task(task_id)
        if actor.actor_type is ActorType.OWNER:
            self._require_owner(
                actor,
                action="CREATE_SUBTASK",
                target_type="TASK",
                target_id=task_id,
            )
        else:
            self._require_task_executive(actor, task, action="CREATE_SUBTASK")
        assigned = self._active_agent(command.assigned_agent_id)
        if assigned.agent_type is AgentType.EXECUTIVE and assigned.id != task.assigned_executive_agent_id:
            raise MvpError("Sub Task cannot silently reassign the Owner Task")
        now = self._now()
        subtask = SubTaskRecord(
            id=self._id("ST").upper(),
            parent_task_id=task_id,
            title=command.title,
            description=command.description,
            assigned_agent_id=assigned.id,
            dependencies=command.dependencies,
            created_at=now,
            updated_at=now,
        )
        self.subtasks[subtask.id] = subtask
        self._audit(
            actor=actor.actor_id,
            actor_type=actor.actor_type.value,
            action="CREATE_SUBTASK",
            target_type="SUB_TASK",
            target_id=subtask.id,
            policy_result=PolicyResult.ALLOW,
            reason="Sub Task created within the Owner Task boundary",
            after=subtask,
        )
        return self._copy(subtask)

    def transition_task(
        self,
        actor: ActorContext,
        task_id: str,
        to_status: TaskStatus,
        *,
        reason: str = "",
    ) -> TaskRecord:
        task = self._task(task_id)
        allowed: dict[TaskStatus, set[TaskStatus]] = {
            TaskStatus.DRAFT: {TaskStatus.APPROVED, TaskStatus.CANCELLED},
            TaskStatus.APPROVED: {TaskStatus.PLANNING, TaskStatus.CANCELLED},
            TaskStatus.PLANNING: {TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED, TaskStatus.CANCELLED},
            TaskStatus.IN_PROGRESS: {TaskStatus.REVIEW, TaskStatus.BLOCKED, TaskStatus.CANCELLED},
            TaskStatus.REVIEW: {TaskStatus.AUDIT, TaskStatus.REWORK, TaskStatus.BLOCKED},
            TaskStatus.AUDIT: {TaskStatus.OWNER_REVIEW, TaskStatus.REWORK, TaskStatus.BLOCKED},
            TaskStatus.OWNER_REVIEW: {TaskStatus.COMPLETED, TaskStatus.REWORK, TaskStatus.REJECTED, TaskStatus.BLOCKED},
            TaskStatus.REWORK: {TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED, TaskStatus.BLOCKED},
            TaskStatus.BLOCKED: {TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED},
            TaskStatus.COMPLETED: set(),
            TaskStatus.REJECTED: set(),
            TaskStatus.CANCELLED: set(),
        }
        if to_status not in allowed[task.status]:
            raise MvpTransitionError(f"Invalid Task transition {task.status.value} -> {to_status.value}")

        owner_targets = {
            TaskStatus.APPROVED,
            TaskStatus.CANCELLED,
            TaskStatus.COMPLETED,
            TaskStatus.REJECTED,
            TaskStatus.REWORK,
        }
        if to_status in owner_targets:
            self._require_owner(
                actor,
                action="TRANSITION_TASK",
                target_type="TASK",
                target_id=task_id,
            )
            if to_status is TaskStatus.COMPLETED and task_id not in self.evaluations:
                raise MvpTransitionError("Owner evaluation is required before completion")
        elif to_status in {TaskStatus.PLANNING, TaskStatus.IN_PROGRESS}:
            self._require_task_executive(actor, task, action="TRANSITION_TASK")
        elif to_status is TaskStatus.REVIEW:
            self._require_task_executive(actor, task, action="TRANSITION_TASK")
        elif to_status is TaskStatus.AUDIT:
            reviewer = self._require_active_agent(
                actor,
                action="TRANSITION_TASK",
                target_type="TASK",
                target_id=task_id,
            )
            self._require_policy(
                actor,
                action="SUBMIT_REVIEW",
                target_type="TASK",
                target_id=task_id,
            )
            if reviewer.role is not AgentRole.REVIEWER:
                self._deny(
                    actor,
                    action="TRANSITION_TASK",
                    target_type="TASK",
                    target_id=task_id,
                    reason="independent Reviewer authority is required",
                )
            if actor.actor_id == task.assigned_executive_agent_id:
                self._deny(
                    actor,
                    action="TRANSITION_TASK",
                    target_type="TASK",
                    target_id=task_id,
                    reason="Reviewer must be independent from the Task executor",
                )
        elif to_status is TaskStatus.OWNER_REVIEW:
            auditor = self._require_active_agent(
                actor,
                action="TRANSITION_TASK",
                target_type="TASK",
                target_id=task_id,
            )
            self._require_policy(
                actor,
                action="RECORD_AUDIT",
                target_type="TASK",
                target_id=task_id,
            )
            if auditor.role is not AgentRole.AUDITOR:
                self._deny(
                    actor,
                    action="TRANSITION_TASK",
                    target_type="TASK",
                    target_id=task_id,
                    reason="independent Auditor authority is required",
                )
            if actor.actor_id == task.assigned_executive_agent_id:
                self._deny(
                    actor,
                    action="TRANSITION_TASK",
                    target_type="TASK",
                    target_id=task_id,
                    reason="Auditor must be independent from the Task executor",
                )
        elif to_status is TaskStatus.BLOCKED:
            if actor.actor_id == task.assigned_executive_agent_id:
                self._require_task_executive(actor, task, action="TRANSITION_TASK")
            elif actor.actor_type is ActorType.AGENT and actor.role is AgentRole.AUDITOR:
                self._require_active_agent(
                    actor,
                    action="TRANSITION_TASK",
                    target_type="TASK",
                    target_id=task_id,
                )
                self._require_policy(
                    actor,
                    action="RECORD_AUDIT",
                    target_type="TASK",
                    target_id=task_id,
                )
            else:
                self._require_owner(
                    actor,
                    action="TRANSITION_TASK",
                    target_type="TASK",
                    target_id=task_id,
                )
        before = self._copy(task)
        task.status = to_status
        task.updated_at = self._now()
        self._audit(
            actor=actor.actor_id,
            actor_type=actor.actor_type.value,
            action="TRANSITION_TASK",
            target_type="TASK",
            target_id=task_id,
            policy_result=PolicyResult.ALLOW,
            reason=reason or f"Task moved to {to_status.value}",
            before=before,
            after=task,
        )
        return self._copy(task)

    def submit_review(
        self,
        actor: ActorContext,
        task_id: str,
        command: ReviewCommand,
    ) -> ReviewRecord:
        task = self._task(task_id)
        reviewer = self._require_active_agent(
            actor,
            action="SUBMIT_REVIEW",
            target_type="TASK",
            target_id=task_id,
        )
        self._require_policy(
            actor,
            action="SUBMIT_REVIEW",
            target_type="TASK",
            target_id=task_id,
        )
        if reviewer.role is not AgentRole.REVIEWER:
            self._deny(
                actor,
                action="SUBMIT_REVIEW",
                target_type="TASK",
                target_id=task_id,
                reason="Reviewer role is required",
            )
        if actor.actor_id == task.assigned_executive_agent_id:
            self._deny(
                actor,
                action="SUBMIT_REVIEW",
                target_type="TASK",
                target_id=task_id,
                reason="Reviewer must be independent from the Task executor",
            )
        if task.status is not TaskStatus.REVIEW:
            raise MvpTransitionError("Review can only be submitted from REVIEW")
        now = self._now()
        review = ReviewRecord(task_id=task_id, reviewer=actor.actor_id, reviewed_at=now, **command.model_dump())
        self.reviews.setdefault(task_id, []).append(review)
        before = self._copy(task)
        task.status = TaskStatus.REWORK if command.required_changes else TaskStatus.AUDIT
        task.updated_at = now
        self._audit(
            actor=actor.actor_id,
            actor_type=actor.actor_type.value,
            action="SUBMIT_REVIEW",
            target_type="TASK",
            target_id=task_id,
            policy_result=PolicyResult.ALLOW,
            reason="Independent review recorded",
            before=before,
            after=task,
        )
        return self._copy(review)

    def record_audit(
        self,
        actor: ActorContext,
        task_id: str,
        command: AuditCommand,
    ) -> AuditRecord:
        task = self._task(task_id)
        auditor = self._require_active_agent(
            actor,
            action="RECORD_AUDIT",
            target_type="TASK",
            target_id=task_id,
        )
        self._require_policy(
            actor,
            action="RECORD_AUDIT",
            target_type="TASK",
            target_id=task_id,
        )
        if auditor.role is not AgentRole.AUDITOR:
            self._deny(
                actor,
                action="RECORD_AUDIT",
                target_type="TASK",
                target_id=task_id,
                reason="Auditor role is required",
            )
        if actor.actor_id == task.assigned_executive_agent_id:
            self._deny(
                actor,
                action="RECORD_AUDIT",
                target_type="TASK",
                target_id=task_id,
                reason="Auditor must be independent from the Task executor",
            )
        if task.status is not TaskStatus.AUDIT:
            raise MvpTransitionError("Audit can only be recorded from AUDIT")
        now = self._now()
        audit = AuditRecord(task_id=task_id, auditor=actor.actor_id, audited_at=now, **command.model_dump())
        self.audits.setdefault(task_id, []).append(audit)
        before = self._copy(task)
        if command.result in {AuditResult.PASS, AuditResult.PASS_WITH_CONDITIONS}:
            task.status = TaskStatus.OWNER_REVIEW
            self.approvals.setdefault(
                f"approval-task-{task_id.casefold()}",
                ApprovalRequest(
                    id=f"approval-task-{task_id.casefold()}",
                    approval_type=ApprovalType.TASK_COMPLETION,
                    target_id=task_id,
                    requested_by=task.assigned_executive_agent_id,
                    created_at=now,
                ),
            )
            for reward in self.rewards.values():
                if reward.task_id == task_id:
                    self.approvals.setdefault(
                        f"approval-reward-{reward.id}",
                        ApprovalRequest(
                            id=f"approval-reward-{reward.id}",
                            approval_type=ApprovalType.REWARD,
                            target_id=reward.id,
                            requested_by=task.assigned_executive_agent_id,
                            created_at=now,
                        ),
                    )
        task.updated_at = now
        self._audit(
            actor=actor.actor_id,
            actor_type=actor.actor_type.value,
            action="RECORD_AUDIT",
            target_type="TASK",
            target_id=task_id,
            policy_result=PolicyResult.ALLOW,
            reason="Audit result recorded; FAIL cannot advance to Owner Review",
            before=before,
            after=task,
        )
        return self._copy(audit)

    def evaluate_task(
        self,
        actor: ActorContext,
        task_id: str,
        command: OwnerEvaluationCommand,
    ) -> OwnerEvaluation:
        task = self._task(task_id)
        self._require_owner(
            actor,
            action="OWNER_EVALUATE_TASK",
            target_type="TASK",
            target_id=task_id,
        )
        if task.status is not TaskStatus.OWNER_REVIEW:
            raise MvpTransitionError("Owner evaluation requires OWNER_REVIEW")
        now = self._now()
        evaluation = OwnerEvaluation(
            task_id=task_id,
            evaluated_by=actor.actor_id,
            evaluated_at=now,
            **command.model_dump(),
        )
        self.evaluations[task_id] = evaluation
        # This is deliberately only a reference proposal. It never approves or pays.
        for reward in self.rewards.values():
            if reward.task_id == task_id and reward.status is RewardStatus.PENDING:
                reward.proposed_reward_lamports = (
                    task.reward_budget_lamports * command.quality // 100
                )
                reward.status = RewardStatus.PROPOSED
        self._audit(
            actor=actor.actor_id,
            actor_type=actor.actor_type.value,
            action="OWNER_EVALUATE_TASK",
            target_type="TASK",
            target_id=task_id,
            policy_result=PolicyResult.ALLOW,
            reason="Owner evaluation recorded; reward remains unapproved",
            after=evaluation,
        )
        return self._copy(evaluation)

    def approve_reward(
        self,
        actor: ActorContext,
        reward_id: str,
        command: RewardApprovalCommand,
    ) -> RewardRecord:
        try:
            reward = self.rewards[reward_id]
        except KeyError as exc:
            raise MvpError(f"reward is not registered: {reward_id}") from exc
        self._require_owner(
            actor,
            action="APPROVE_REWARD",
            target_type="REWARD",
            target_id=reward_id,
        )
        task = self._task(reward.task_id)
        if reward.task_id not in self.evaluations:
            raise MvpTransitionError("Owner evaluation is required before Final Reward")
        if task.status not in {TaskStatus.OWNER_REVIEW, TaskStatus.COMPLETED}:
            raise MvpTransitionError("Final Reward requires Owner Review or a completed Task")
        if reward.status not in {RewardStatus.PENDING, RewardStatus.PROPOSED}:
            raise MvpTransitionError("Reward allocation is no longer pending approval")
        if command.approved_reward_lamports > reward.reward_budget_lamports and not command.reason.strip():
            raise MvpError("reason is required when Final Reward exceeds Reward Budget")
        before = self._copy(reward)
        reward.approved_reward_lamports = command.approved_reward_lamports
        reward.approved_by = actor.actor_id
        reward.approved_at = self._now()
        reward.comment = command.reason
        reward.status = RewardStatus.APPROVED
        self.reward_ledgers[reward.id] = RewardLedger(
            id=f"ledger-{reward.id}",
            allocation_id=reward.id,
            task_id=reward.task_id,
            agent_id=reward.agent_id,
            amount_lamports=reward.approved_reward_lamports,
            status=RewardStatus.APPROVED,
            recorded_by=actor.actor_id,
            created_at=reward.approved_at,
        )
        self._audit(
            actor=actor.actor_id,
            actor_type=actor.actor_type.value,
            action="APPROVE_REWARD",
            target_type="REWARD",
            target_id=reward_id,
            policy_result=PolicyResult.ALLOW,
            reason="Owner explicitly approved a virtual Reward allocation",
            before=before,
            after=reward,
        )
        return self._copy(reward)

    def decide_approval(
        self,
        actor: ActorContext,
        approval_id: str,
        command: ApprovalDecisionCommand,
    ) -> ApprovalRequest:
        try:
            approval = self.approvals[approval_id]
        except KeyError as exc:
            raise MvpError(f"approval request is not registered: {approval_id}") from exc
        self._require_owner(
            actor,
            action="DECIDE_APPROVAL",
            target_type="APPROVAL_REQUEST",
            target_id=approval_id,
        )
        if approval.owner_decision is not None:
            raise MvpTransitionError("Approval Request already has an Owner decision")
        if approval.approval_type is ApprovalType.TASK_COMPLETION:
            if command.decision is ApprovalDecision.APPROVE:
                if approval.target_id not in self.evaluations:
                    raise MvpTransitionError("Owner evaluation is required before completion")
                self.transition_task(actor, approval.target_id, TaskStatus.COMPLETED, reason=command.comment)
            elif command.decision is ApprovalDecision.REQUEST_CHANGES:
                self.transition_task(actor, approval.target_id, TaskStatus.REWORK, reason=command.comment)
            elif command.decision is ApprovalDecision.REJECT:
                self.transition_task(actor, approval.target_id, TaskStatus.REJECTED, reason=command.comment)
        elif approval.approval_type is ApprovalType.REWARD:
            reward = self.rewards[approval.target_id]
            if command.decision is ApprovalDecision.APPROVE:
                self.approve_reward(
                    actor,
                    reward.id,
                    RewardApprovalCommand(
                        approved_reward_lamports=reward.proposed_reward_lamports,
                        reason=command.comment,
                    ),
                )
            elif command.decision is ApprovalDecision.REJECT:
                before_reward = self._copy(reward)
                reward.status = RewardStatus.CANCELLED
                reward.cancelled_reward_lamports = reward.proposed_reward_lamports
                self._audit(
                    actor=actor.actor_id,
                    actor_type=actor.actor_type.value,
                    action="CANCEL_REWARD",
                    target_type="REWARD",
                    target_id=reward.id,
                    policy_result=PolicyResult.ALLOW,
                    reason=command.comment or "Owner rejected the proposed Reward",
                    before=before_reward,
                    after=reward,
                )
        elif approval.approval_type is ApprovalType.BOARD_PROPOSAL:
            self.decide_proposal(actor, approval.target_id, command.decision, comment=command.comment)
        elif approval.approval_type is ApprovalType.EXTERNAL_ACTION:
            self.decide_external_action(actor, approval.target_id, command.decision, comment=command.comment)
        before = self._copy(approval)
        approval.owner_decision = command.decision
        approval.comment = command.comment
        approval.decided_at = self._now()
        self._audit(
            actor=actor.actor_id,
            actor_type=actor.actor_type.value,
            action="DECIDE_APPROVAL",
            target_type="APPROVAL_REQUEST",
            target_id=approval_id,
            policy_result=PolicyResult.ALLOW,
            reason=command.comment or "Owner recorded a unified Approval Center decision",
            before=before,
            after=approval,
        )
        return self._copy(approval)

    def create_proposal(
        self,
        actor: ActorContext,
        command: ProposalCreateCommand,
    ) -> BoardProposal:
        if actor.actor_type is ActorType.OWNER:
            self._require_owner(
                actor,
                action="CREATE_BOARD_PROPOSAL",
                target_type="BOARD_PROPOSAL",
                target_id="new",
            )
        else:
            self._require_active_agent(
                actor,
                action="CREATE_BOARD_PROPOSAL",
                target_type="BOARD_PROPOSAL",
                target_id="new",
            )
            self._require_policy(
                actor,
                action="CREATE_BOARD_PROPOSAL",
                target_type="BOARD_PROPOSAL",
                target_id="new",
            )
        now = self._now()
        proposal = BoardProposal(
            id=self._id("proposal"),
            proposer=actor.actor_id,
            created_at=now,
            updated_at=now,
            **command.model_dump(),
        )
        self.proposals[proposal.id] = proposal
        self.approvals[f"approval-{proposal.id}"] = ApprovalRequest(
            id=f"approval-{proposal.id}",
            approval_type=ApprovalType.BOARD_PROPOSAL,
            target_id=proposal.id,
            requested_by=actor.actor_id,
            created_at=now,
        )
        self._audit(
            actor=actor.actor_id,
            actor_type=actor.actor_type.value,
            action="CREATE_BOARD_PROPOSAL",
            target_type="BOARD_PROPOSAL",
            target_id=proposal.id,
            policy_result=PolicyResult.ALLOW,
            reason="Proposal submitted for Owner decision",
            after=proposal,
        )
        return self._copy(proposal)

    def decide_proposal(
        self,
        actor: ActorContext,
        proposal_id: str,
        decision: ApprovalDecision,
        *,
        comment: str = "",
    ) -> BoardProposal:
        try:
            proposal = self.proposals[proposal_id]
        except KeyError as exc:
            raise MvpError(f"proposal is not registered: {proposal_id}") from exc
        self._require_owner(
            actor,
            action="DECIDE_BOARD_PROPOSAL",
            target_type="BOARD_PROPOSAL",
            target_id=proposal_id,
        )
        if proposal.owner_decision is not None:
            raise MvpTransitionError("Board Proposal already has an Owner decision")
        before = self._copy(proposal)
        proposal.owner_decision = decision
        proposal.status = decision.value
        proposal.updated_at = self._now()
        self._audit(
            actor=actor.actor_id,
            actor_type=actor.actor_type.value,
            action="DECIDE_BOARD_PROPOSAL",
            target_type="BOARD_PROPOSAL",
            target_id=proposal_id,
            policy_result=PolicyResult.ALLOW,
            reason=comment or "Owner recorded Board Proposal decision",
            before=before,
            after=proposal,
        )
        return self._copy(proposal)

    def create_external_action(
        self,
        actor: ActorContext,
        command: ExternalActionCreateCommand,
    ) -> ExternalActionRequest:
        task = self._task(command.task_id) if command.task_id else None
        if actor.actor_type is ActorType.OWNER:
            self._require_owner(
                actor,
                action="REQUEST_EXTERNAL_ACTION",
                target_type="EXTERNAL_ACTION",
                target_id="new",
            )
        else:
            self._require_active_agent(
                actor,
                action="REQUEST_EXTERNAL_ACTION",
                target_type="EXTERNAL_ACTION",
                target_id="new",
            )
            self._require_policy(
                actor,
                action="REQUEST_EXTERNAL_ACTION",
                target_type="EXTERNAL_ACTION",
                target_id="new",
            )
            if task is None:
                self._deny(
                    actor,
                    action="REQUEST_EXTERNAL_ACTION",
                    target_type="EXTERNAL_ACTION",
                    target_id="new",
                    reason="external Action requests from Agents must be Task-bound",
                )
            self._require_task_member(actor, task, action="REQUEST_EXTERNAL_ACTION")
        if task is not None and not task.external_action_allowed:
            request_id = self._id("external")
            self._audit(
                actor=actor.actor_id,
                actor_type=actor.actor_type.value,
                action="REQUEST_EXTERNAL_ACTION",
                target_type="EXTERNAL_ACTION",
                target_id=request_id,
                policy_result=PolicyResult.DENY,
                reason="Task does not allow External Action requests",
            )
            raise MvpAuthorizationError("Task does not allow External Action requests")
        policy = PolicyResult.OWNER_APPROVAL_REQUIRED
        now = self._now()
        payload = command.model_dump()
        payload["expires_at"] = self._as_utc(command.expires_at)
        request = ExternalActionRequest(
            id=self._id("external"),
            requested_by=actor.actor_id,
            created_at=now,
            **payload,
        )
        self.external_actions[request.id] = request
        self.approvals[f"approval-{request.id}"] = ApprovalRequest(
            id=f"approval-{request.id}",
            approval_type=ApprovalType.EXTERNAL_ACTION,
            target_id=request.id,
            requested_by=actor.actor_id,
            created_at=now,
        )
        self._audit(
            actor=actor.actor_id,
            actor_type=actor.actor_type.value,
            action="REQUEST_EXTERNAL_ACTION",
            target_type="EXTERNAL_ACTION",
            target_id=request.id,
            policy_result=policy,
            reason="External Action is queued for explicit Owner approval; no send occurs",
            after=request,
        )
        return self._copy(request)

    def decide_external_action(
        self,
        actor: ActorContext,
        request_id: str,
        decision: ApprovalDecision,
        *,
        comment: str = "",
    ) -> ExternalActionRequest:
        try:
            request = self.external_actions[request_id]
        except KeyError as exc:
            raise MvpError(f"external Action is not registered: {request_id}") from exc
        self._require_owner(
            actor,
            action="DECIDE_EXTERNAL_ACTION",
            target_type="EXTERNAL_ACTION",
            target_id=request_id,
        )
        if request.owner_decision is not None:
            raise MvpTransitionError("External Action already has an Owner decision")
        before = self._copy(request)
        request.owner_decision = decision
        request.status = (
            ExternalActionStatus.APPROVED
            if decision is ApprovalDecision.APPROVE
            else ExternalActionStatus.REJECTED
            if decision is ApprovalDecision.REJECT
            else ExternalActionStatus.PENDING
        )
        request.execution_result = comment or "Owner decision recorded; external executor is disabled in MVP"
        self._audit(
            actor=actor.actor_id,
            actor_type=actor.actor_type.value,
            action="DECIDE_EXTERNAL_ACTION",
            target_type="EXTERNAL_ACTION",
            target_id=request_id,
            policy_result=PolicyResult.ALLOW,
            reason=comment or "Owner recorded External Action decision",
            before=before,
            after=request,
        )
        return self._copy(request)

    def check_external_action_scope(
        self,
        actor: ActorContext,
        request_id: str,
        command: ExternalActionScopeCommand,
    ) -> dict[str, Any]:
        try:
            request = self.external_actions[request_id]
        except KeyError as exc:
            raise MvpError(f"external Action is not registered: {request_id}") from exc
        task = self._task(request.task_id) if request.task_id else None
        if actor.actor_type is ActorType.OWNER:
            self._require_owner(
                actor,
                action="DECIDE_EXTERNAL_ACTION",
                target_type="EXTERNAL_ACTION",
                target_id=request_id,
            )
        else:
            if task is None:
                self._deny(
                    actor,
                    action="CHECK_EXTERNAL_ACTION_SCOPE",
                    target_type="EXTERNAL_ACTION",
                    target_id=request_id,
                    reason="Agent scope checks require a Task-bound request",
                )
            self._require_task_member(
                actor,
                task,
                action="CHECK_EXTERNAL_ACTION_SCOPE",
            )
        now = self._now()
        if request.expires_at <= now and request.status is ExternalActionStatus.APPROVED:
            request.status = ExternalActionStatus.EXPIRED
        allowed = (
            (task is None or task.external_action_allowed)
            and
            request.owner_decision is ApprovalDecision.APPROVE
            and request.status is ExternalActionStatus.APPROVED
            and request.expires_at > now
            and request.execution_count < request.allowed_action_count
            and request.recipient == command.recipient
            and request.channel is command.channel
            and request.content == command.content
        )
        result = PolicyResult.ALLOW_WITH_SCOPE if allowed else PolicyResult.DENY
        self._audit(
            actor=actor.actor_id,
            actor_type=actor.actor_type.value,
            action="CHECK_EXTERNAL_ACTION_SCOPE",
            target_type="EXTERNAL_ACTION",
            target_id=request_id,
            policy_result=result,
            reason="Scope matches Owner approval" if allowed else "Scope, approval, expiry, or count does not match",
        )
        if not allowed:
            raise MvpAuthorizationError("external Action is outside the approved scope")
        return {
            "policy_result": result.value,
            "request_id": request_id,
            "execution": "NOT_EXECUTED_MVP",
            "remaining_action_count": request.allowed_action_count - request.execution_count,
        }
