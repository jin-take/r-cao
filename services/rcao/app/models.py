from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class AgentRole(str, Enum):
    OWNER = "OWNER"
    STRATEGY = "STRATEGY"
    PRODUCT = "PRODUCT"
    ENGINEERING = "ENGINEERING"
    OPERATIONS = "OPERATIONS"
    MANAGER = "MANAGER"
    RESEARCHER = "RESEARCHER"
    BUILDER = "BUILDER"
    REVIEWER = "REVIEWER"
    TREASURY = "TREASURY"
    AUDITOR = "AUDITOR"


class TaskState(str, Enum):
    DRAFT = "DRAFT"
    ISSUED = "ISSUED"
    IN_PROGRESS = "IN_PROGRESS"
    IN_REVIEW = "IN_REVIEW"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    REWARDED = "REWARDED"
    CANCELLED = "CANCELLED"


class MessageType(str, Enum):
    COMMAND = "COMMAND"
    DELEGATION = "DELEGATION"
    REQUEST = "REQUEST"
    RESPONSE = "RESPONSE"
    HANDOFF = "HANDOFF"
    REVIEW_REQUEST = "REVIEW_REQUEST"
    REVIEW_RESULT = "REVIEW_RESULT"
    BLOCK = "BLOCK"
    ESCALATION = "ESCALATION"
    DECISION_REQUEST = "DECISION_REQUEST"
    OWNER_DECISION = "OWNER_DECISION"
    EVIDENCE = "EVIDENCE"


class MessageStatus(str, Enum):
    """Durable lifecycle for a Task-bound A2A message."""

    SENT = "SENT"
    DELIVERED = "DELIVERED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    CONSUMED = "CONSUMED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class Agent(BaseModel):
    id: str
    name: str
    role: AgentRole
    capability_hash: str
    model: str
    status: str = "ACTIVE"
    reputation: int = Field(default=0, ge=0, le=100)
    rank: int = Field(default=1, ge=1)


class Task(BaseModel):
    id: str
    title: str
    description: str
    reward_lamports: int = Field(ge=0)
    difficulty: int = Field(ge=1, le=5)
    state: TaskState = TaskState.DRAFT
    deadline: str
    acceptance_criteria: list[str] = Field(min_length=1)
    issued_by: str | None = None


class TaskAssignment(BaseModel):
    task_id: str
    agent_id: str
    role: AgentRole
    contribution_score: int = Field(gt=0, le=100)


class Evaluation(BaseModel):
    task_id: str
    reviewer_id: str
    quality: int = Field(ge=0, le=100)
    risk: int = Field(ge=0, le=100)
    comment: str
    final_score: int = Field(ge=0, le=100)


class RewardContribution(BaseModel):
    agent_id: str
    contribution_score: int = Field(gt=0, le=100)


class RewardAllocation(BaseModel):
    agent_id: str
    amount_lamports: int = Field(ge=0)


class RewardResult(BaseModel):
    allocations: list[RewardAllocation]
    retained_lamports: int = Field(ge=0)


class TreasuryProposal(BaseModel):
    id: str
    proposal_type: str
    amount_lamports: int = Field(gt=0)
    expected_roi_bps: int
    risk: int = Field(ge=1, le=5)
    status: str = "SUBMITTED"
    approval_by: str | None = None


class AuthorityContext(BaseModel):
    delegation_id: str | None = None
    allowed_scope: list[str] = Field(default_factory=list)
    budget_lamports: int = Field(default=0, ge=0)
    risk_class: str = "LOW"
    expires_at: str | None = None


class AgentMessage(BaseModel):
    schema_version: str = "1.0"
    message_id: str
    idempotency_key: str = Field(min_length=1)
    nonce: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    trace_id: str
    task_id: str | None = None
    run_id: str | None = None
    conversation_id: str | None = None
    parent_message_id: str | None = None
    sender_agent_id: str
    recipient_agent_id: str
    message_type: MessageType
    authority_context: AuthorityContext = Field(default_factory=AuthorityContext)
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    expires_at: str | None = None
    correlation_id: str | None = None
    status: MessageStatus = MessageStatus.SENT


class PhaseOneSimulationInput(BaseModel):
    task: Task
    assignments: list[TaskAssignment] = Field(min_length=2)
    evaluation: Evaluation
    owner_id: str
    treasury_agent_id: str


class PhaseOneSimulationResult(BaseModel):
    task: Task
    reward: RewardResult
    treasury_proposal: TreasuryProposal
    audit_actions: list[str]
