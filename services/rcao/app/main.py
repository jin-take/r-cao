import os

from fastapi import Depends, FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .agent_runtime import runtime_integration_notes
from .auth import (
    ActorContext,
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
from .search import InMemoryOperationSearch, SearchQuery, SearchResponse, SearchScope


class HealthResponse(BaseModel):
    service: str
    phase: int
    ledger: str
    status: str


app = FastAPI(
    title="R-CAO Control Plane",
    version="0.4.0",
    description="Owner-directed control plane and Agent runtime boundary.",
)

operation_search = InMemoryOperationSearch()
mvp_store = OwnerDirectedStore(
    owner_id=os.getenv("RCAO_OWNER_ID", "owner-local"),
    owner_name=os.getenv("RCAO_OWNER_NAME", "Owner"),
)


@app.exception_handler(MvpAuthorizationError)
async def mvp_authorization_error(_, exc: MvpAuthorizationError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(MvpError)
async def mvp_error(_, exc: MvpError) -> JSONResponse:
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
def dashboard() -> dict:
    return mvp_store.dashboard()


@app.get("/api/v1/agents", response_model=list[AgentRecord])
def list_agents(include_sub_agents: bool = Query(default=True)) -> list[AgentRecord]:
    return mvp_store.list_agents(include_sub_agents=include_sub_agents)


@app.get("/api/v1/agents/{agent_id}", response_model=AgentRecord)
def get_agent(agent_id: str) -> AgentRecord:
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
) -> AgentRecord:
    return mvp_store.set_agent_status(actor, agent_id, command)


@app.get("/api/v1/tasks", response_model=list[TaskRecord])
def list_tasks(status: TaskStatus | None = Query(default=None)) -> list[TaskRecord]:
    return mvp_store.list_tasks(status)


@app.get("/api/v1/tasks/{task_id}", response_model=TaskDetail)
def get_task(task_id: str) -> TaskDetail:
    return mvp_store.get_task_detail(task_id)


@app.post("/api/v1/tasks", response_model=TaskRecord)
def create_task(
    command: TaskCreateCommand,
    actor: ActorContext = Depends(require_owner_actor),
) -> TaskRecord:
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
) -> TaskRecord:
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


@app.post("/api/v1/tasks/{task_id}/evaluation", response_model=OwnerEvaluation)
def evaluate_task(
    task_id: str,
    command: OwnerEvaluationCommand,
    actor: ActorContext = Depends(require_owner_actor),
) -> OwnerEvaluation:
    return mvp_store.evaluate_task(actor, task_id, command)


@app.get("/api/v1/approvals", response_model=list[ApprovalRequest])
def list_approvals() -> list[ApprovalRequest]:
    return mvp_store.list_approvals()


@app.post("/api/v1/approvals/{approval_id}/decision", response_model=ApprovalRequest)
def decide_approval(
    approval_id: str,
    command: ApprovalDecisionCommand,
    actor: ActorContext = Depends(require_owner_actor),
) -> ApprovalRequest:
    return mvp_store.decide_approval(actor, approval_id, command)


@app.get("/api/v1/rewards", response_model=list[RewardRecord])
def list_rewards() -> list[RewardRecord]:
    return mvp_store.list_rewards()


@app.post("/api/v1/rewards/{reward_id}/approve", response_model=RewardRecord)
def approve_reward(
    reward_id: str,
    command: RewardApprovalCommand,
    actor: ActorContext = Depends(require_owner_actor),
) -> RewardRecord:
    return mvp_store.approve_reward(actor, reward_id, command)


@app.get("/api/v1/proposals", response_model=list[BoardProposal])
def list_proposals() -> list[BoardProposal]:
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
def list_external_actions() -> list[ExternalActionRequest]:
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
def list_audit(limit: int = Query(default=200, ge=1, le=1000)) -> list[AuditLogRecord]:
    return mvp_store.list_audit_logs(limit=limit)


@app.get("/api/v1/settings/policies")
def policy_catalog() -> list[dict[str, str]]:
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
) -> SearchResponse:
    """Read-only operations search; PostgreSQL is the next repository adapter."""

    return operation_search.search(
        SearchQuery(
            q=q,
            scope=scope,
            task_id=task_id,
            run_id=run_id,
            agent_id=agent_id,
            status=status,
            limit=limit,
        )
    )
