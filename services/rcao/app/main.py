import os
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .a2a import (
    MessageAuthorizationError,
    MessageGatewayError,
    MessageSendResponse,
    MessageStatusCommand,
    MessageStatusResponse,
    PersistentMessageGateway,
)
from .agent_registry import AgentRegistryError, AgentRegistryRepository
from .agent_runtime import runtime_integration_notes
from .auth import (
    ActorContext,
    ActorType,
    PolicyCheckRequest,
    PolicyCheckResponse,
    evaluate_actor_policy,
    require_actor,
    require_owner_actor,
)
from .mvp import (
    AgentCreateCommand,
    AgentRecord,
    AgentStatusCommand,
    ApprovalDecision,
    ApprovalDecisionCommand,
    ApprovalRequest,
    AssignExecutiveCommand,
    AuditCommand,
    AuditRecord,
    AuditLogRecord,
    BoardProposal,
    ExternalActionCreateCommand,
    ExternalActionRequest,
    ExternalActionScopeCommand,
    MvpAuthorizationError,
    MvpError,
    OwnerDirectedStore,
    OwnerEvaluation,
    OwnerEvaluationCommand,
    ProposalCreateCommand,
    RewardApprovalCommand,
    RewardRecord,
    ReviewCommand,
    ReviewRecord,
    SubTaskCreateCommand,
    SubTaskRecord,
    TaskCreateCommand,
    TaskDetail,
    TaskRecord,
    TaskStatus,
    TaskStatusCommand,
)
from .console import PersistentConsoleReadModel
from .models import AgentMessage, MessageStatus
from .search import InMemoryOperationSearch, SearchQuery, SearchResponse, SearchScope
from .task_workflow import (
    PersistentTaskWorkflow,
    TaskWorkflowRepository,
    TaskWorkflowError,
    postgres_task_workflow,
)
from .virtual_ledger import VirtualLedgerError


class HealthResponse(BaseModel):
    service: str
    phase: int
    ledger: str
    status: str


class AcceptanceCriteriaCommand(BaseModel):
    acceptance_criteria: list[str]
    reason: str


class EvidenceCommand(BaseModel):
    sub_task_id: str
    uri: str
    content_hash: str | None = None


class TreasuryFundingCommand(BaseModel):
    amount_lamports: int = Field(gt=0)
    reason: str = Field(min_length=1)


class RewardPaymentCommand(BaseModel):
    retention_bps: int = Field(default=0, ge=0, le=10_000)
    reason: str = Field(default="Owner released the approved virtual Reward", min_length=1)


app = FastAPI(
    title="R-CAO Control Plane",
    version="0.4.0",
    description="Owner-directed control plane and Agent runtime boundary.",
)


def _console_origins() -> list[str]:
    configured = os.getenv(
        "RCAO_CONSOLE_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    )
    origins = [origin.strip() for origin in configured.split(",") if origin.strip()]
    if not origins or "*" in origins:
        raise RuntimeError("RCAO_CONSOLE_ORIGINS must contain explicit origins")
    return origins


app.add_middleware(
    CORSMiddleware,
    allow_origins=_console_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
)

operation_search = InMemoryOperationSearch()
mvp_store = OwnerDirectedStore(
    owner_id=os.getenv("RCAO_OWNER_ID", "owner-local"),
    owner_name=os.getenv("RCAO_OWNER_NAME", "Owner"),
)


def _persistent_workflow_from_environment() -> PersistentTaskWorkflow | None:
    """Enable durable command routes only when explicitly selected."""

    if os.getenv("RCAO_TASK_BACKEND", "memory").lower() != "postgres":
        return None
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required when RCAO_TASK_BACKEND=postgres")
    return postgres_task_workflow(
        database_url,
        owner_id=os.getenv("RCAO_OWNER_ID", "owner-local"),
    )


persistent_task_workflow = _persistent_workflow_from_environment()
persistent_console_read_model = (
    PersistentConsoleReadModel(
        persistent_task_workflow.repository,
        annual_budget_lamports=int(
            os.getenv("RCAO_ANNUAL_BUDGET_LAMPORTS", "12500000000")
        ),
    )
    if persistent_task_workflow is not None
    else None
)


@app.exception_handler(MvpAuthorizationError)
async def mvp_authorization_error(_, exc: MvpAuthorizationError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(MvpError)
async def mvp_error(_, exc: MvpError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(TaskWorkflowError)
async def task_workflow_error(_, exc: TaskWorkflowError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(VirtualLedgerError)
async def virtual_ledger_error(_, exc: VirtualLedgerError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(AgentRegistryError)
async def agent_registry_error(_, exc: AgentRegistryError) -> JSONResponse:
    """Turn Registry authorization failures into controlled API responses."""

    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(MessageAuthorizationError)
async def message_authorization_error(_, exc: MessageAuthorizationError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(MessageGatewayError)
async def message_gateway_error(_, exc: MessageGatewayError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        service="rcao-control-plane",
        phase=1,
        ledger="virtual",
        status="ok",
    )


@app.get("/api/v1/auth/me", response_model=ActorContext)
def current_actor(actor: ActorContext = Depends(require_actor)) -> ActorContext:
    """Return the canonical Actor Context established by the bearer token."""

    return actor


@app.post("/api/v1/auth/policy-check", response_model=PolicyCheckResponse)
def policy_check(
    request: PolicyCheckRequest,
    actor: ActorContext = Depends(require_actor),
) -> PolicyCheckResponse:
    """Evaluate a proposed action without executing a state-changing command."""

    decision, reason = evaluate_actor_policy(
        actor,
        request.action,
        task_id=request.task_id,
    )
    return PolicyCheckResponse(
        actor_id=actor.actor_id,
        action=request.action,
        task_id=request.task_id,
        decision=decision,
        reason=reason,
    )


@app.get("/api/v1/dashboard")
def dashboard(actor: ActorContext = Depends(require_owner_actor)) -> dict:
    if persistent_console_read_model is not None:
        return persistent_console_read_model.dashboard()
    return mvp_store.dashboard()


@app.get("/api/v1/agents", response_model=list[AgentRecord])
def list_agents(
    include_sub_agents: bool = Query(default=True),
    actor: ActorContext = Depends(require_owner_actor),
) -> list[AgentRecord]:
    if persistent_console_read_model is not None:
        return persistent_console_read_model.list_agents(
            include_sub_agents=include_sub_agents
        )
    return mvp_store.list_agents(include_sub_agents=include_sub_agents)


@app.get("/api/v1/agents/{agent_id}", response_model=AgentRecord)
def get_agent(
    agent_id: str,
    actor: ActorContext = Depends(require_owner_actor),
) -> AgentRecord:
    if persistent_console_read_model is not None:
        return persistent_console_read_model.get_agent(agent_id)
    return mvp_store.get_agent(agent_id)


@app.post("/api/v1/agents", response_model=AgentRecord)
def create_agent(
    command: AgentCreateCommand,
    actor: ActorContext = Depends(require_owner_actor),
) -> AgentRecord:
    return mvp_store.create_agent(actor, command)


@app.post("/api/v1/agents/{agent_id}/status", response_model=AgentRecord)
def change_agent_status(
    agent_id: str,
    command: AgentStatusCommand,
    actor: ActorContext = Depends(require_owner_actor),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentRecord:
    if persistent_task_workflow is not None and persistent_console_read_model is not None:
        def persist_status(transaction):
            return AgentRegistryRepository(transaction).set_status(
                actor_id=actor.actor_id,
                actor_type=actor.actor_type.value,
                agent_id=agent_id,
                status=command.status.value,
                audit_id=f"audit-{idempotency_key or uuid4().hex}",
                correlation_id=f"corr-{uuid4().hex}",
                reason=command.reason or "Owner changed Agent status",
            )

        persistent_task_workflow._run(persist_status)
        return persistent_console_read_model.get_agent(agent_id)
    return mvp_store.set_agent_status(actor, agent_id, command)


@app.get("/api/v1/tasks", response_model=list[TaskRecord])
def list_tasks(
    status: TaskStatus | None = Query(default=None),
    actor: ActorContext = Depends(require_owner_actor),
) -> list[TaskRecord]:
    if persistent_console_read_model is not None:
        return persistent_console_read_model.list_tasks(status.value if status else None)
    return mvp_store.list_tasks(status)


@app.get("/api/v1/tasks/{task_id}", response_model=TaskDetail)
def get_task(
    task_id: str,
    actor: ActorContext = Depends(require_owner_actor),
) -> TaskDetail:
    if persistent_console_read_model is not None:
        return persistent_console_read_model.get_task_detail(task_id)
    return mvp_store.get_task_detail(task_id)


@app.post("/api/v1/tasks", response_model=TaskRecord)
def create_task(
    command: TaskCreateCommand,
    actor: ActorContext = Depends(require_owner_actor),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> TaskRecord:
    if persistent_task_workflow is not None:
        return persistent_task_workflow.create_task(
            actor,
            command,
            idempotency_key=idempotency_key,
        )
    return mvp_store.create_task(actor, command)


@app.post("/api/v1/tasks/{task_id}/assign", response_model=TaskRecord)
def assign_task(
    task_id: str,
    command: AssignExecutiveCommand,
    actor: ActorContext = Depends(require_owner_actor),
) -> TaskRecord:
    return mvp_store.assign_executive(actor, task_id, command.executive_agent_id)


@app.post("/api/v1/tasks/{task_id}/subtasks", response_model=SubTaskRecord)
def create_subtask(
    task_id: str,
    command: SubTaskCreateCommand,
    actor: ActorContext = Depends(require_actor),
) -> SubTaskRecord:
    return mvp_store.create_subtask(actor, task_id, command)


@app.post("/api/v1/tasks/{task_id}/status", response_model=TaskRecord)
def transition_task(
    task_id: str,
    command: TaskStatusCommand,
    actor: ActorContext = Depends(require_actor),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> TaskRecord:
    if persistent_task_workflow is not None:
        return persistent_task_workflow.transition_task(
            actor,
            task_id,
            command.status,
            reason=command.reason,
            idempotency_key=idempotency_key,
        )
    return mvp_store.transition_task(actor, task_id, command.status, reason=command.reason)


@app.post("/api/v1/tasks/{task_id}/review", response_model=ReviewRecord)
def submit_review(
    task_id: str,
    command: ReviewCommand,
    actor: ActorContext = Depends(require_actor),
) -> ReviewRecord:
    return mvp_store.submit_review(actor, task_id, command)


@app.post("/api/v1/tasks/{task_id}/audit", response_model=AuditRecord)
def record_audit(
    task_id: str,
    command: AuditCommand,
    actor: ActorContext = Depends(require_actor),
) -> AuditRecord:
    return mvp_store.record_audit(actor, task_id, command)


def _require_persistent_workflow() -> PersistentTaskWorkflow:
    if persistent_task_workflow is None:
        raise HTTPException(
            status_code=503,
            detail="persistent Task workflow is disabled; set RCAO_TASK_BACKEND=postgres",
        )
    return persistent_task_workflow


def _require_message_gateway() -> PersistentMessageGateway:
    """Reuse the Task workflow's DB composition for the A2A gateway."""

    return PersistentMessageGateway(_require_persistent_workflow().repository)


@app.post("/api/v1/messages", response_model=MessageSendResponse)
def send_agent_message(
    message: AgentMessage,
    actor: ActorContext = Depends(require_actor),
) -> MessageSendResponse:
    """Persist a validated Task-bound proposal for another registered Agent."""

    if actor.actor_type is not ActorType.AGENT or actor.actor_id != message.sender_agent_id:
        raise MessageAuthorizationError(
            "the authenticated Agent must match sender_agent_id"
        )
    result = _require_message_gateway().send(message)
    return MessageSendResponse(message=result.message, replayed=result.replayed)


@app.post("/api/v1/messages/{message_id}/status", response_model=MessageStatusResponse)
def update_agent_message_status(
    message_id: str,
    command: MessageStatusCommand,
    actor: ActorContext = Depends(require_actor),
) -> MessageStatusResponse:
    """Advance a message lifecycle only by its registered recipient Agent."""

    result = _require_message_gateway().transition_status(
        message_id,
        actor=actor,
        status=command.status,
        reason=command.reason,
    )
    return MessageStatusResponse(message=result.message, replayed=result.replayed)


@app.get("/api/v1/messages", response_model=list[AgentMessage])
def list_agent_messages(
    task_id: str | None = Query(default=None),
    trace_id: str | None = Query(default=None),
    conversation_id: str | None = Query(default=None),
    status: MessageStatus | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    actor: ActorContext = Depends(require_actor),
) -> list[AgentMessage]:
    """Search messages by their safe correlation fields."""

    return list(
        _require_message_gateway().list_messages(
            actor=actor,
            task_id=task_id,
            trace_id=trace_id,
            conversation_id=conversation_id,
            status=status,
            limit=limit,
            offset=offset,
        )
    )


@app.post("/api/v1/commands/tasks", response_model=TaskRecord)
def create_persistent_task(
    command: TaskCreateCommand,
    actor: ActorContext = Depends(require_owner_actor),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> TaskRecord:
    """Create a durable Owner Task when the PostgreSQL backend is selected."""

    return _require_persistent_workflow().create_task(
        actor,
        command,
        idempotency_key=idempotency_key,
    )


@app.post("/api/v1/commands/tasks/{task_id}/acceptance-criteria", response_model=TaskRecord)
def update_persistent_acceptance_criteria(
    task_id: str,
    command: AcceptanceCriteriaCommand,
    actor: ActorContext = Depends(require_owner_actor),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> TaskRecord:
    return _require_persistent_workflow().update_acceptance_criteria(
        actor,
        task_id,
        command.acceptance_criteria,
        reason=command.reason,
        idempotency_key=idempotency_key,
    )


@app.post("/api/v1/commands/tasks/{task_id}/assign", response_model=TaskRecord)
def assign_persistent_executive(
    task_id: str,
    command: AssignExecutiveCommand,
    actor: ActorContext = Depends(require_owner_actor),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> TaskRecord:
    return _require_persistent_workflow().assign_executive(
        actor,
        task_id,
        command.executive_agent_id,
        idempotency_key=idempotency_key,
    )


@app.post("/api/v1/commands/tasks/{task_id}/subtasks", response_model=SubTaskRecord)
def create_persistent_subtask(
    task_id: str,
    command: SubTaskCreateCommand,
    actor: ActorContext = Depends(require_actor),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> SubTaskRecord:
    return _require_persistent_workflow().create_subtask(
        actor,
        task_id,
        command,
        idempotency_key=idempotency_key,
    )


@app.post("/api/v1/commands/tasks/{task_id}/status", response_model=TaskRecord)
def transition_persistent_task(
    task_id: str,
    command: TaskStatusCommand,
    actor: ActorContext = Depends(require_actor),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> TaskRecord:
    return _require_persistent_workflow().transition_task(
        actor,
        task_id,
        command.status,
        reason=command.reason,
        idempotency_key=idempotency_key,
    )


@app.post("/api/v1/commands/tasks/{task_id}/evidence", response_model=SubTaskRecord)
def submit_persistent_evidence(
    task_id: str,
    command: EvidenceCommand,
    actor: ActorContext = Depends(require_actor),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> SubTaskRecord:
    return _require_persistent_workflow().submit_evidence(
        actor,
        task_id,
        command.sub_task_id,
        command.uri,
        content_hash=command.content_hash,
        idempotency_key=idempotency_key,
    )


@app.post("/api/v1/commands/tasks/{task_id}/review", response_model=ReviewRecord)
def submit_persistent_review(
    task_id: str,
    command: ReviewCommand,
    actor: ActorContext = Depends(require_actor),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ReviewRecord:
    return _require_persistent_workflow().submit_review(
        actor,
        task_id,
        command,
        idempotency_key=idempotency_key,
    )


@app.post("/api/v1/commands/tasks/{task_id}/audit", response_model=AuditRecord)
def record_persistent_audit(
    task_id: str,
    command: AuditCommand,
    actor: ActorContext = Depends(require_actor),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AuditRecord:
    return _require_persistent_workflow().record_audit(
        actor,
        task_id,
        command,
        idempotency_key=idempotency_key,
    )


@app.post("/api/v1/tasks/{task_id}/evaluation", response_model=OwnerEvaluation)
def evaluate_task(
    task_id: str,
    command: OwnerEvaluationCommand,
    actor: ActorContext = Depends(require_owner_actor),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> OwnerEvaluation:
    if persistent_task_workflow is not None:
        return persistent_task_workflow.evaluate_task(
            actor,
            task_id,
            command,
            idempotency_key=idempotency_key,
        )
    return mvp_store.evaluate_task(actor, task_id, command)


@app.post("/api/v1/commands/tasks/{task_id}/evaluation", response_model=OwnerEvaluation)
def evaluate_persistent_task(
    task_id: str,
    command: OwnerEvaluationCommand,
    actor: ActorContext = Depends(require_owner_actor),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> OwnerEvaluation:
    return _require_persistent_workflow().evaluate_task(
        actor,
        task_id,
        command,
        idempotency_key=idempotency_key,
    )


@app.post("/api/v1/commands/treasury/fund")
def fund_persistent_treasury(
    command: TreasuryFundingCommand,
    actor: ActorContext = Depends(require_owner_actor),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    """Add virtual Reward capacity; this endpoint never moves real assets."""

    return _require_persistent_workflow().fund_treasury(
        actor,
        command.amount_lamports,
        reason=command.reason,
        idempotency_key=idempotency_key,
    ).to_payload()


@app.get("/api/v1/ledger/reconcile")
def reconcile_persistent_treasury(
    actor: ActorContext = Depends(require_owner_actor),
) -> dict:
    """Recalculate the virtual Treasury and fail closed on an invariant break."""

    return _require_persistent_workflow().reconcile_treasury(actor).to_payload()


@app.post("/api/v1/commands/rewards/{allocation_id}/pay")
def pay_persistent_reward(
    allocation_id: str,
    command: RewardPaymentCommand,
    actor: ActorContext = Depends(require_owner_actor),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    """Release a reserved virtual Reward after explicit Owner approval."""

    return _require_persistent_workflow().pay_reward(
        actor,
        allocation_id,
        retention_bps=command.retention_bps,
        reason=command.reason,
        idempotency_key=idempotency_key,
    ).to_payload()


@app.get("/api/v1/approvals", response_model=list[ApprovalRequest])
def list_approvals(
    actor: ActorContext = Depends(require_owner_actor),
) -> list[ApprovalRequest]:
    if persistent_console_read_model is not None:
        return persistent_console_read_model.list_approvals()
    return mvp_store.list_approvals()


@app.post("/api/v1/approvals/{approval_id}/decision", response_model=ApprovalRequest)
def decide_approval(
    approval_id: str,
    command: ApprovalDecisionCommand,
    actor: ActorContext = Depends(require_owner_actor),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ApprovalRequest:
    if persistent_task_workflow is not None:
        return persistent_task_workflow.decide_approval(
            actor,
            approval_id,
            command.decision,
            comment=command.comment,
            idempotency_key=idempotency_key,
        )
    return mvp_store.decide_approval(actor, approval_id, command)


@app.post("/api/v1/commands/approvals/{approval_id}/decision", response_model=ApprovalRequest)
def decide_persistent_approval(
    approval_id: str,
    command: ApprovalDecisionCommand,
    actor: ActorContext = Depends(require_owner_actor),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ApprovalRequest:
    return _require_persistent_workflow().decide_approval(
        actor,
        approval_id,
        command.decision,
        comment=command.comment,
        idempotency_key=idempotency_key,
    )


@app.get("/api/v1/rewards", response_model=list[RewardRecord])
def list_rewards(
    actor: ActorContext = Depends(require_owner_actor),
) -> list[RewardRecord]:
    if persistent_console_read_model is not None:
        return persistent_console_read_model.list_rewards()
    return mvp_store.list_rewards()


@app.post("/api/v1/rewards/{reward_id}/approve", response_model=RewardRecord)
def approve_reward(
    reward_id: str,
    command: RewardApprovalCommand,
    actor: ActorContext = Depends(require_owner_actor),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> RewardRecord:
    if persistent_task_workflow is not None and persistent_console_read_model is not None:
        def approve_persistent_reward(transaction):
            row = transaction.fetch_one(
                """
                SELECT id FROM approval_requests
                WHERE approval_type = 'REWARD'::mvp_approval_type
                  AND target_id = %s
                  AND owner_decision IS NULL
                ORDER BY created_at ASC, id ASC
                LIMIT 1
                """,
                (reward_id,),
            )
            if row is None:
                raise TaskWorkflowError(
                    f"pending Reward approval is not registered: {reward_id}"
                )
            return TaskWorkflowRepository(
                transaction,
                owner_id=persistent_task_workflow.owner_id,
            ).decide_approval(
                actor,
                str(row[0]),
                ApprovalDecision.APPROVE,
                comment=command.reason,
                reward_amount_lamports=command.approved_reward_lamports,
                idempotency_key=idempotency_key,
            )

        approve_persistent_reward_result = persistent_task_workflow._run(
            approve_persistent_reward
        )
        del approve_persistent_reward_result
        reward = next(
            (
                item
                for item in persistent_console_read_model.list_rewards()
                if item.id == reward_id
            ),
            None,
        )
        if reward is None:
            raise TaskWorkflowError(f"Reward is not registered: {reward_id}")
        return reward
    return mvp_store.approve_reward(actor, reward_id, command)


@app.get("/api/v1/proposals", response_model=list[BoardProposal])
def list_proposals(
    actor: ActorContext = Depends(require_owner_actor),
) -> list[BoardProposal]:
    if persistent_console_read_model is not None:
        return persistent_console_read_model.list_proposals()
    return mvp_store.list_proposals()


@app.post("/api/v1/proposals", response_model=BoardProposal)
def create_proposal(
    command: ProposalCreateCommand,
    actor: ActorContext = Depends(require_actor),
) -> BoardProposal:
    return mvp_store.create_proposal(actor, command)


@app.post("/api/v1/proposals/{proposal_id}/decision", response_model=BoardProposal)
def decide_proposal(
    proposal_id: str,
    command: ApprovalDecisionCommand,
    actor: ActorContext = Depends(require_owner_actor),
) -> BoardProposal:
    return mvp_store.decide_proposal(actor, proposal_id, command.decision, comment=command.comment)


@app.get("/api/v1/external-actions", response_model=list[ExternalActionRequest])
def list_external_actions(
    actor: ActorContext = Depends(require_owner_actor),
) -> list[ExternalActionRequest]:
    if persistent_console_read_model is not None:
        return persistent_console_read_model.list_external_actions()
    return mvp_store.list_external_actions()


@app.post("/api/v1/external-actions", response_model=ExternalActionRequest)
def request_external_action(
    command: ExternalActionCreateCommand,
    actor: ActorContext = Depends(require_actor),
) -> ExternalActionRequest:
    return mvp_store.create_external_action(actor, command)


@app.post("/api/v1/external-actions/{request_id}/decision", response_model=ExternalActionRequest)
def decide_external_action(
    request_id: str,
    command: ApprovalDecisionCommand,
    actor: ActorContext = Depends(require_owner_actor),
) -> ExternalActionRequest:
    return mvp_store.decide_external_action(actor, request_id, command.decision, comment=command.comment)


@app.post("/api/v1/external-actions/{request_id}/scope-check")
def check_external_action_scope(
    request_id: str,
    command: ExternalActionScopeCommand,
    actor: ActorContext = Depends(require_actor),
) -> dict:
    return mvp_store.check_external_action_scope(actor, request_id, command)


@app.get("/api/v1/audit", response_model=list[AuditLogRecord])
def list_audit(
    limit: int = Query(default=200, ge=1, le=1000),
    actor: ActorContext = Depends(require_owner_actor),
) -> list[AuditLogRecord]:
    if persistent_console_read_model is not None:
        return persistent_console_read_model.list_audit_logs(limit=limit)
    return mvp_store.list_audit_logs(limit=limit)


@app.get("/api/v1/settings/policies")
def policy_catalog(
    actor: ActorContext = Depends(require_owner_actor),
) -> list[dict[str, str]]:
    return mvp_store.policy_catalog()


@app.get("/api/v1/capabilities")
def capabilities() -> dict[str, dict[str, str]]:
    return {"runtime": runtime_integration_notes()}


@app.get("/api/v1/operations/search", response_model=SearchResponse)
def search_operations(
    q: str = Query(default=""),
    scope: SearchScope = Query(default=SearchScope.ALL),
    task_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    agent_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    actor: ActorContext = Depends(require_owner_actor),
) -> SearchResponse:
    """Read-only operations search over the configured control-plane read model."""

    query = SearchQuery(
        q=q,
        scope=scope,
        task_id=task_id,
        run_id=run_id,
        agent_id=agent_id,
        status=status,
        limit=limit,
    )
    if persistent_console_read_model is not None:
        return persistent_console_read_model.search_operations(query)
    return operation_search.search(query)
