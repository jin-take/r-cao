"""Virtual Reward Ledger and Treasury invariants.

This module is intentionally separate from MPP and on-chain payments.  It
records only the Owner-directed virtual Reward economy.  A Ledger entry is a
durable accounting fact; it does not move SOL, SPL tokens, or any customer
asset.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from .audit import AuditEvent, AuditWriter, OutboxEvent, OutboxWriter
from .auth import ActorContext, ActorType
from .repository import RepositoryTransaction


VIRTUAL_REWARD_ASSET = "VIRTUAL_REWARD"
VIRTUAL_CURRENCY = "VIRTUAL"
DEFAULT_TREASURY_ACCOUNT = "virtual-reward-treasury"
MAX_RETENTION_BPS = 10_000
MAX_SIGNED_BIGINT = (1 << 63) - 1


class VirtualLedgerError(ValueError):
    """Base error for Virtual Ledger validation and persistence."""


class LedgerAuthorizationError(VirtualLedgerError):
    """The actor is not allowed to mutate the Treasury or Reward Ledger."""


class LedgerInvariantError(VirtualLedgerError):
    """A stored balance or Ledger sequence is inconsistent."""


class LedgerConflict(VirtualLedgerError):
    """The requested accounting operation conflicts with current state."""


class DirectAgentTransferError(VirtualLedgerError):
    """Direct Agent-to-Agent Reward or asset transfers are forbidden."""


@dataclass(frozen=True)
class VirtualLedgerEntry:
    entry_id: str
    account_id: str
    entry_type: str
    status: str
    amount_lamports: int
    asset_type: str
    currency: str
    task_id: str | None
    allocation_id: str | None
    agent_id: str | None
    calculation_version: str
    idempotency_key: str
    recorded_by: str
    correlation_id: str
    created_at: datetime | str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "account_id": self.account_id,
            "entry_type": self.entry_type,
            "status": self.status,
            "amount_lamports": self.amount_lamports,
            "asset_type": self.asset_type,
            "currency": self.currency,
            "task_id": self.task_id,
            "allocation_id": self.allocation_id,
            "agent_id": self.agent_id,
            "calculation_version": self.calculation_version,
            "idempotency_key": self.idempotency_key,
            "recorded_by": self.recorded_by,
            "correlation_id": self.correlation_id,
            "created_at": self.created_at.isoformat()
            if isinstance(self.created_at, datetime)
            else self.created_at,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "VirtualLedgerEntry":
        """Restore an idempotent command response without querying the DB."""

        return cls(
            entry_id=str(payload["entry_id"]),
            account_id=str(payload["account_id"]),
            entry_type=str(payload["entry_type"]),
            status=str(payload["status"]),
            amount_lamports=int(payload["amount_lamports"]),
            asset_type=str(payload["asset_type"]),
            currency=str(payload["currency"]),
            task_id=str(payload["task_id"]) if payload.get("task_id") is not None else None,
            allocation_id=(
                str(payload["allocation_id"])
                if payload.get("allocation_id") is not None
                else None
            ),
            agent_id=str(payload["agent_id"]) if payload.get("agent_id") is not None else None,
            calculation_version=str(payload["calculation_version"]),
            idempotency_key=str(payload["idempotency_key"]),
            recorded_by=str(payload["recorded_by"]),
            correlation_id=str(payload["correlation_id"]),
            created_at=payload.get("created_at"),
        )


@dataclass(frozen=True)
class TreasuryBalance:
    account_id: str
    asset_type: str
    currency: str
    funded_lamports: int
    available_lamports: int
    reserved_lamports: int
    paid_lamports: int
    retained_lamports: int
    version: int

    @property
    def total_accounted_lamports(self) -> int:
        return (
            self.available_lamports
            + self.reserved_lamports
            + self.paid_lamports
            + self.retained_lamports
        )


@dataclass(frozen=True)
class ReconciliationResult:
    stored: TreasuryBalance
    calculated_funded_lamports: int
    calculated_available_lamports: int
    calculated_reserved_lamports: int
    calculated_paid_lamports: int
    calculated_retained_lamports: int
    healthy: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "account_id": self.stored.account_id,
            "asset_type": self.stored.asset_type,
            "currency": self.stored.currency,
            "stored": {
                "funded_lamports": self.stored.funded_lamports,
                "available_lamports": self.stored.available_lamports,
                "reserved_lamports": self.stored.reserved_lamports,
                "paid_lamports": self.stored.paid_lamports,
                "retained_lamports": self.stored.retained_lamports,
            },
            "calculated": {
                "funded_lamports": self.calculated_funded_lamports,
                "available_lamports": self.calculated_available_lamports,
                "reserved_lamports": self.calculated_reserved_lamports,
                "paid_lamports": self.calculated_paid_lamports,
                "retained_lamports": self.calculated_retained_lamports,
            },
            "healthy": self.healthy,
        }


class VirtualLedgerPolicy:
    """Pure policy checks for virtual accounting commands."""

    @staticmethod
    def require_owner(actor: ActorContext, owner_id: str) -> None:
        if (
            actor.actor_type is not ActorType.OWNER
            or actor.actor_id != owner_id
            or actor.role.value != "OWNER"
        ):
            raise LedgerAuthorizationError("Virtual Ledger changes require the canonical Owner")

    @staticmethod
    def validate_amount(amount_lamports: int, *, allow_zero: bool = True) -> None:
        if type(amount_lamports) is not int:
            raise VirtualLedgerError("Ledger amounts must be integers")
        if (
            amount_lamports < 0
            or amount_lamports > MAX_SIGNED_BIGINT
            or (not allow_zero and amount_lamports == 0)
        ):
            raise VirtualLedgerError(
                "Ledger amounts must be within the signed BIGINT range and non-zero for this operation"
            )

    @staticmethod
    def validate_retention_bps(retention_bps: int) -> None:
        if type(retention_bps) is not int or not 0 <= retention_bps <= MAX_RETENTION_BPS:
            raise VirtualLedgerError("retention_bps must be between 0 and 10000")

    @staticmethod
    def reject_direct_agent_transfer(*, sender_agent_id: str, recipient_agent_id: str) -> None:
        if sender_agent_id and recipient_agent_id:
            raise DirectAgentTransferError(
                "direct Agent-to-Agent Reward and asset transfers are not supported"
            )

    @staticmethod
    def calculate_retention(amount_lamports: int, retention_bps: int) -> tuple[int, int]:
        VirtualLedgerPolicy.validate_amount(amount_lamports)
        VirtualLedgerPolicy.validate_retention_bps(retention_bps)
        retained = amount_lamports * retention_bps // MAX_RETENTION_BPS
        return amount_lamports - retained, retained


def _row_value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, Mapping):
        return row[key]
    return row[index]


def _as_datetime(value: Any) -> datetime | str | None:
    return value


def _entry_from_row(row: Any) -> VirtualLedgerEntry:
    values = (
        dict(row)
        if isinstance(row, Mapping)
        else dict(
            zip(
                (
                    "id",
                    "account_id",
                    "entry_type",
                    "status",
                    "amount_lamports",
                    "asset_type",
                    "currency",
                    "task_id",
                    "allocation_id",
                    "agent_id",
                    "calculation_version",
                    "idempotency_key",
                    "recorded_by",
                    "correlation_id",
                    "created_at",
                ),
                row,
                strict=True,
            )
        )
    )
    return VirtualLedgerEntry(
        entry_id=str(values["id"]),
        account_id=str(values["account_id"]),
        entry_type=str(values["entry_type"]),
        status=str(values["status"]),
        amount_lamports=int(values["amount_lamports"]),
        asset_type=str(values["asset_type"]),
        currency=str(values["currency"]),
        task_id=str(values["task_id"]) if values.get("task_id") is not None else None,
        allocation_id=str(values["allocation_id"]) if values.get("allocation_id") is not None else None,
        agent_id=str(values["agent_id"]) if values.get("agent_id") is not None else None,
        calculation_version=str(values["calculation_version"]),
        idempotency_key=str(values["idempotency_key"]),
        recorded_by=str(values["recorded_by"]),
        correlation_id=str(values["correlation_id"]),
        created_at=_as_datetime(values.get("created_at")),
    )


def _balance_from_row(row: Any) -> TreasuryBalance:
    values = (
        dict(row)
        if isinstance(row, Mapping)
        else dict(
            zip(
                (
                    "id",
                    "asset_type",
                    "currency",
                    "funded_lamports",
                    "available_lamports",
                    "reserved_lamports",
                    "paid_lamports",
                    "retained_lamports",
                    "version",
                ),
                row,
                strict=True,
            )
        )
    )
    return TreasuryBalance(
        account_id=str(values["id"]),
        asset_type=str(values["asset_type"]),
        currency=str(values["currency"]),
        funded_lamports=int(values["funded_lamports"]),
        available_lamports=int(values["available_lamports"]),
        reserved_lamports=int(values["reserved_lamports"]),
        paid_lamports=int(values["paid_lamports"]),
        retained_lamports=int(values["retained_lamports"]),
        version=int(values["version"]),
    )


class VirtualLedgerRepository:
    """Atomic Treasury and virtual Reward accounting operations."""

    def __init__(
        self,
        transaction: RepositoryTransaction,
        *,
        owner_id: str = "owner-local",
        account_id: str = DEFAULT_TREASURY_ACCOUNT,
    ) -> None:
        self.transaction = transaction
        self.owner_id = owner_id
        self.account_id = account_id

    def _ensure_account(self) -> None:
        self.transaction.execute(
            """
            INSERT INTO mvp_treasury_accounts
              (id, asset_type, currency)
            VALUES (%s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (self.account_id, VIRTUAL_REWARD_ASSET, VIRTUAL_CURRENCY),
        )

    def _balance(self, *, for_update: bool = False) -> TreasuryBalance:
        lock = " FOR UPDATE" if for_update else ""
        row = self.transaction.fetch_one(
            f"""
            SELECT id, asset_type, currency, funded_lamports,
                   available_lamports, reserved_lamports, paid_lamports,
                   retained_lamports, version
            FROM mvp_treasury_accounts
            WHERE id = %s{lock}
            """,
            (self.account_id,),
        )
        if row is None:
            raise LedgerInvariantError("Virtual Treasury account is not initialized")
        balance = _balance_from_row(row)
        if balance.asset_type != VIRTUAL_REWARD_ASSET or balance.currency != VIRTUAL_CURRENCY:
            raise LedgerInvariantError("Virtual Treasury asset or currency is invalid")
        if min(
            balance.funded_lamports,
            balance.available_lamports,
            balance.reserved_lamports,
            balance.paid_lamports,
            balance.retained_lamports,
        ) < 0:
            raise LedgerInvariantError("Virtual Treasury balance cannot be negative")
        if balance.total_accounted_lamports != balance.funded_lamports:
            raise LedgerInvariantError("Virtual Treasury balance invariant is broken")
        return balance

    def _existing_entry(self, idempotency_key: str) -> VirtualLedgerEntry | None:
        row = self.transaction.fetch_one(
            """
            SELECT id, account_id, entry_type, status, amount_lamports,
                   asset_type, currency, task_id, allocation_id, agent_id,
                   calculation_version, idempotency_key, recorded_by,
                   correlation_id, created_at
            FROM mvp_virtual_ledger_entries
            WHERE idempotency_key = %s
            FOR UPDATE
            """,
            (idempotency_key,),
        )
        return _entry_from_row(row) if row is not None else None

    @staticmethod
    def _assert_replay_matches(
        existing: VirtualLedgerEntry,
        *,
        entry_type: str,
        amount_lamports: int | None = None,
        allocation_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        """Reject reuse of a key for a materially different accounting fact."""

        if (
            existing.entry_type != entry_type
            or (amount_lamports is not None and existing.amount_lamports != amount_lamports)
            or (allocation_id is not None and existing.allocation_id != allocation_id)
            or (task_id is not None and existing.task_id != task_id)
        ):
            raise LedgerConflict("Ledger idempotency key is bound to a different entry")

    def _emit(
        self,
        *,
        actor: ActorContext,
        action: str,
        target_id: str,
        task_id: str | None,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
        reason: str,
        idempotency_key: str,
    ) -> str:
        correlation_id = f"corr-{uuid4().hex}"
        AuditWriter.append(
            self.transaction,
            AuditEvent(
                event_id=f"audit-{uuid4().hex}",
                event_version=1,
                event_type="VIRTUAL_LEDGER_COMMAND",
                actor_id=actor.actor_id,
                actor_type=actor.actor_type.value,
                action=action,
                target_type="VIRTUAL_LEDGER",
                target_id=target_id,
                before_state=before,
                after_state=after,
                policy_result="ALLOW",
                reason=reason,
                correlation_id=correlation_id,
                transaction_id=correlation_id,
                task_id=task_id,
                ledger_entry_id=target_id,
            ),
        )
        OutboxWriter.enqueue(
            self.transaction,
            OutboxEvent(
                event_id=f"outbox-{uuid4().hex}",
                aggregate_type="VIRTUAL_LEDGER",
                aggregate_id=target_id,
                event_type=action,
                idempotency_key=idempotency_key,
                payload={
                    "action": action,
                    "ledger_entry_id": target_id,
                    "task_id": task_id,
                    "before": dict(before),
                    "after": dict(after),
                },
                event_version=1,
                transaction_id=correlation_id,
            ),
        )
        return correlation_id

    def _insert_entry(
        self,
        *,
        entry_type: str,
        status: str,
        amount_lamports: int,
        task_id: str | None,
        allocation_id: str | None,
        agent_id: str | None,
        calculation_version: str,
        idempotency_key: str,
        recorded_by: str,
        correlation_id: str,
    ) -> VirtualLedgerEntry:
        VirtualLedgerPolicy.validate_amount(amount_lamports)
        if not calculation_version:
            raise VirtualLedgerError("calculation_version is required")
        existing = self._existing_entry(idempotency_key)
        if existing is not None:
            if (
                existing.entry_type != entry_type
                or existing.amount_lamports != amount_lamports
                or existing.task_id != task_id
                or existing.allocation_id != allocation_id
            ):
                raise LedgerConflict("Ledger idempotency key is bound to a different entry")
            return existing
        entry_id = f"ledger-{uuid4().hex}"
        self.transaction.execute(
            """
            INSERT INTO mvp_virtual_ledger_entries
              (id, account_id, entry_type, status, amount_lamports,
               asset_type, currency, task_id, allocation_id, agent_id,
               calculation_version, idempotency_key, recorded_by, correlation_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s)
            """,
            (
                entry_id,
                self.account_id,
                entry_type,
                status,
                amount_lamports,
                VIRTUAL_REWARD_ASSET,
                VIRTUAL_CURRENCY,
                task_id,
                allocation_id,
                agent_id,
                calculation_version,
                idempotency_key,
                recorded_by,
                correlation_id,
            ),
        )
        row = self.transaction.fetch_one(
            """
            SELECT id, account_id, entry_type, status, amount_lamports,
                   asset_type, currency, task_id, allocation_id, agent_id,
                   calculation_version, idempotency_key, recorded_by,
                   correlation_id, created_at
            FROM mvp_virtual_ledger_entries WHERE id = %s
            """,
            (entry_id,),
        )
        if row is None:
            raise LedgerInvariantError("Virtual Ledger entry was not persisted")
        return _entry_from_row(row)

    def fund_treasury(
        self,
        actor: ActorContext,
        amount_lamports: int,
        *,
        idempotency_key: str,
        reason: str,
        calculation_version: str = "treasury-funding-v1",
    ) -> VirtualLedgerEntry:
        VirtualLedgerPolicy.require_owner(actor, self.owner_id)
        VirtualLedgerPolicy.validate_amount(amount_lamports, allow_zero=False)
        self._ensure_account()
        existing = self._existing_entry(idempotency_key)
        if existing is not None:
            self._assert_replay_matches(
                existing,
                entry_type="TREASURY_FUNDING",
                amount_lamports=amount_lamports,
            )
            return existing
        before = self._balance(for_update=True)
        if before.funded_lamports > MAX_SIGNED_BIGINT - amount_lamports:
            raise LedgerConflict("Virtual Treasury funding exceeds the signed BIGINT range")
        self.transaction.execute(
            """
            UPDATE mvp_treasury_accounts
            SET funded_lamports = funded_lamports + %s,
                available_lamports = available_lamports + %s,
                version = version + 1,
                updated_at = now()
            WHERE id = %s AND version = %s
            """,
            (amount_lamports, amount_lamports, self.account_id, before.version),
        )
        correlation_id = f"corr-{uuid4().hex}"
        entry = self._insert_entry(
            entry_type="TREASURY_FUNDING",
            status="Approved",
            amount_lamports=amount_lamports,
            task_id=None,
            allocation_id=None,
            agent_id=None,
            calculation_version=calculation_version,
            idempotency_key=idempotency_key,
            recorded_by=actor.actor_id,
            correlation_id=correlation_id,
        )
        after = self._balance()
        self._emit(
            actor=actor,
            action="FUND_VIRTUAL_TREASURY",
            target_id=entry.entry_id,
            task_id=None,
            before=before.__dict__,
            after=after.__dict__,
            reason=reason,
            idempotency_key=f"{idempotency_key}:audit",
        )
        return entry

    def reserve_reward(
        self,
        actor: ActorContext,
        allocation_id: str,
        amount_lamports: int,
        *,
        idempotency_key: str,
        calculation_version: str,
        reason: str,
    ) -> VirtualLedgerEntry:
        VirtualLedgerPolicy.require_owner(actor, self.owner_id)
        VirtualLedgerPolicy.validate_amount(amount_lamports)
        self._ensure_account()
        existing = self._existing_entry(idempotency_key)
        if existing is not None:
            self._assert_replay_matches(
                existing,
                entry_type="REWARD_RESERVE",
                amount_lamports=amount_lamports,
                allocation_id=allocation_id,
            )
            return existing
        allocation = self.transaction.fetch_one(
            """
            SELECT id::text, task_id, agent_id, reward_budget_lamports,
                   proposed_reward_lamports, approved_reward_lamports,
                   reserved_reward_lamports, status
            FROM reward_allocations WHERE id::text = %s FOR UPDATE
            """,
            (allocation_id,),
        )
        if allocation is None:
            raise LedgerConflict("Reward allocation is not registered")
        allocation_status = str(_row_value(allocation, "status", 7))
        # A replay with the same key returns above.  A new key must not reserve
        # an allocation that is already reserved, otherwise the Treasury and
        # allocation balances could be inflated independently.
        if allocation_status != "Proposed":
            raise LedgerConflict("Reward allocation is not awaiting Owner approval")
        budget = int(_row_value(allocation, "reward_budget_lamports", 3))
        if amount_lamports > budget:
            raise LedgerConflict("Approved Reward exceeds its Task budget")
        before = self._balance(for_update=True)
        if amount_lamports > before.available_lamports:
            raise LedgerConflict("Virtual Treasury has insufficient available balance")
        self.transaction.execute(
            """
            UPDATE mvp_treasury_accounts
            SET available_lamports = available_lamports - %s,
                reserved_lamports = reserved_lamports + %s,
                version = version + 1,
                updated_at = now()
            WHERE id = %s AND version = %s
            """,
            (amount_lamports, amount_lamports, self.account_id, before.version),
        )
        self.transaction.execute(
            """
            UPDATE reward_allocations
            SET approved_reward_lamports = %s,
                reserved_reward_lamports = %s,
                status = 'Reserved'::mvp_reward_status,
                approved_by = %s,
                approved_at = now(),
                comment = %s
            WHERE id::text = %s
            """,
            (amount_lamports, amount_lamports, actor.actor_id, reason, allocation_id),
        )
        self.transaction.execute(
            """
            INSERT INTO reward_ledger
              (id, allocation_id, task_id, agent_id, amount_lamports, status, recorded_by)
            VALUES (%s, %s, %s, %s, %s, 'Reserved'::mvp_reward_status, %s)
            """,
            (
                str(uuid4()),
                allocation_id,
                str(_row_value(allocation, "task_id", 1)),
                str(_row_value(allocation, "agent_id", 2)),
                amount_lamports,
                actor.actor_id,
            ),
        )
        correlation_id = f"corr-{uuid4().hex}"
        entry = self._insert_entry(
            entry_type="REWARD_RESERVE",
            status="Reserved",
            amount_lamports=amount_lamports,
            task_id=str(_row_value(allocation, "task_id", 1)),
            allocation_id=allocation_id,
            agent_id=str(_row_value(allocation, "agent_id", 2)),
            calculation_version=calculation_version,
            idempotency_key=idempotency_key,
            recorded_by=actor.actor_id,
            correlation_id=correlation_id,
        )
        after = self._balance()
        self._emit(
            actor=actor,
            action="RESERVE_REWARD",
            target_id=entry.entry_id,
            task_id=entry.task_id,
            before=before.__dict__,
            after=after.__dict__,
            reason=reason,
            idempotency_key=f"{idempotency_key}:audit",
        )
        return entry

    def pay_reward(
        self,
        actor: ActorContext,
        allocation_id: str,
        *,
        idempotency_key: str,
        retention_bps: int = 0,
        reason: str = "Owner released the approved virtual Reward",
        calculation_version: str = "reward-payment-v1",
    ) -> VirtualLedgerEntry:
        VirtualLedgerPolicy.require_owner(actor, self.owner_id)
        VirtualLedgerPolicy.validate_retention_bps(retention_bps)
        self._ensure_account()
        existing = self._existing_entry(f"{idempotency_key}:payment")
        if existing is not None:
            self._assert_replay_matches(
                existing,
                entry_type="REWARD_PAYMENT",
                allocation_id=allocation_id,
            )
            return existing
        allocation = self.transaction.fetch_one(
            """
            SELECT id::text, task_id, agent_id, approved_reward_lamports,
                   reserved_reward_lamports, status
            FROM reward_allocations WHERE id::text = %s FOR UPDATE
            """,
            (allocation_id,),
        )
        if allocation is None:
            raise LedgerConflict("Reward allocation is not registered")
        status = str(_row_value(allocation, "status", 5))
        if status != "Reserved":
            raise LedgerConflict("only a Reserved Reward can be paid")
        gross = int(_row_value(allocation, "reserved_reward_lamports", 4))
        if gross < 0:
            raise LedgerInvariantError("reserved Reward cannot be negative")
        net, retained = VirtualLedgerPolicy.calculate_retention(gross, retention_bps)
        before = self._balance(for_update=True)
        if gross > before.reserved_lamports:
            raise LedgerInvariantError("Treasury reserved balance is below the Reward allocation")
        self.transaction.execute(
            """
            UPDATE mvp_treasury_accounts
            SET reserved_lamports = reserved_lamports - %s,
                paid_lamports = paid_lamports + %s,
                retained_lamports = retained_lamports + %s,
                version = version + 1,
                updated_at = now()
            WHERE id = %s AND version = %s
            """,
            (gross, net, retained, self.account_id, before.version),
        )
        self.transaction.execute(
            """
            UPDATE reward_allocations
            SET reserved_reward_lamports = 0,
                paid_reward_lamports = paid_reward_lamports + %s,
                status = 'Paid'::mvp_reward_status
            WHERE id::text = %s
            """,
            (net, allocation_id),
        )
        correlation_id = f"corr-{uuid4().hex}"
        payment_entry = self._insert_entry(
            entry_type="REWARD_PAYMENT",
            status="Paid",
            amount_lamports=net,
            task_id=str(_row_value(allocation, "task_id", 1)),
            allocation_id=allocation_id,
            agent_id=str(_row_value(allocation, "agent_id", 2)),
            calculation_version=calculation_version,
            idempotency_key=f"{idempotency_key}:payment",
            recorded_by=actor.actor_id,
            correlation_id=correlation_id,
        )
        if retained:
            self._insert_entry(
                entry_type="TREASURY_RETENTION",
                status="Paid",
                amount_lamports=retained,
                task_id=str(_row_value(allocation, "task_id", 1)),
                allocation_id=allocation_id,
                agent_id=None,
                calculation_version=calculation_version,
                idempotency_key=f"{idempotency_key}:retention",
                recorded_by=actor.actor_id,
                correlation_id=correlation_id,
            )
        self.transaction.execute(
            """
            INSERT INTO reward_ledger
              (id, allocation_id, task_id, agent_id, amount_lamports, status, recorded_by)
            VALUES (%s, %s, %s, %s, %s, 'Paid'::mvp_reward_status, %s)
            """,
            (
                str(uuid4()),
                allocation_id,
                str(_row_value(allocation, "task_id", 1)),
                str(_row_value(allocation, "agent_id", 2)),
                net,
                actor.actor_id,
            ),
        )
        after = self._balance()
        self._emit(
            actor=actor,
            action="PAY_VIRTUAL_REWARD",
            target_id=payment_entry.entry_id,
            task_id=payment_entry.task_id,
            before=before.__dict__,
            after={**after.__dict__, "retained_from_payment_lamports": retained},
            reason=reason,
            idempotency_key=f"{idempotency_key}:audit",
        )
        return payment_entry

    def cancel_reward(
        self,
        actor: ActorContext,
        allocation_id: str,
        *,
        idempotency_key: str,
        reason: str = "Owner cancelled the virtual Reward",
        calculation_version: str = "reward-cancellation-v1",
    ) -> VirtualLedgerEntry | None:
        VirtualLedgerPolicy.require_owner(actor, self.owner_id)
        self._ensure_account()
        existing = self._existing_entry(idempotency_key)
        if existing is not None:
            return existing
        allocation = self.transaction.fetch_one(
            """
            SELECT id::text, task_id, agent_id, proposed_reward_lamports,
                   reserved_reward_lamports, status
            FROM reward_allocations WHERE id::text = %s FOR UPDATE
            """,
            (allocation_id,),
        )
        if allocation is None:
            raise LedgerConflict("Reward allocation is not registered")
        status = str(_row_value(allocation, "status", 5))
        if status in {"Paid", "Cancelled"}:
            raise LedgerConflict("Reward allocation is already final")
        reserved = int(_row_value(allocation, "reserved_reward_lamports", 4))
        before = self._balance(for_update=True)
        if reserved > before.reserved_lamports:
            raise LedgerInvariantError("Treasury reserved balance is below the Reward allocation")
        if reserved:
            self.transaction.execute(
                """
                UPDATE mvp_treasury_accounts
                SET reserved_lamports = reserved_lamports - %s,
                    available_lamports = available_lamports + %s,
                    version = version + 1,
                    updated_at = now()
                WHERE id = %s AND version = %s
                """,
                (reserved, reserved, self.account_id, before.version),
            )
        self.transaction.execute(
            """
            UPDATE reward_allocations
            SET reserved_reward_lamports = 0,
                cancelled_reward_lamports = cancelled_reward_lamports + %s,
                status = 'Cancelled'::mvp_reward_status,
                comment = %s
            WHERE id::text = %s
            """,
            (reserved, reason, allocation_id),
        )
        if not reserved:
            return None
        correlation_id = f"corr-{uuid4().hex}"
        entry = self._insert_entry(
            entry_type="REWARD_CANCELLED",
            status="Cancelled",
            amount_lamports=reserved,
            task_id=str(_row_value(allocation, "task_id", 1)),
            allocation_id=allocation_id,
            agent_id=str(_row_value(allocation, "agent_id", 2)),
            calculation_version=calculation_version,
            idempotency_key=idempotency_key,
            recorded_by=actor.actor_id,
            correlation_id=correlation_id,
        )
        after = self._balance()
        self._emit(
            actor=actor,
            action="CANCEL_VIRTUAL_REWARD",
            target_id=entry.entry_id,
            task_id=entry.task_id,
            before=before.__dict__,
            after=after.__dict__,
            reason=reason,
            idempotency_key=f"{idempotency_key}:audit",
        )
        return entry

    def reconcile(self) -> ReconciliationResult:
        self._ensure_account()
        stored = self._balance()
        rows = self.transaction.fetch_all(
            """
            SELECT entry_type, COALESCE(SUM(amount_lamports), 0) AS amount
            FROM mvp_virtual_ledger_entries
            WHERE account_id = %s
            GROUP BY entry_type
            """,
            (self.account_id,),
        )
        totals = {
            str(_row_value(row, "entry_type", 0)): int(_row_value(row, "amount", 1))
            for row in rows
        }
        funding = totals.get("TREASURY_FUNDING", 0)
        reserves = totals.get("REWARD_RESERVE", 0)
        releases = totals.get("REWARD_RELEASED", 0) + totals.get("REWARD_CANCELLED", 0)
        payments = totals.get("REWARD_PAYMENT", 0)
        retention = totals.get("TREASURY_RETENTION", 0)
        calculated_reserved = reserves - releases - payments - retention
        calculated_available = funding - reserves + releases
        calculated_paid = payments
        calculated_funded = funding
        calculated_retained = retention
        healthy = (
            min(
                calculated_funded,
                calculated_available,
                calculated_reserved,
                calculated_paid,
                calculated_retained,
            )
            >= 0
            and stored.funded_lamports == calculated_funded
            and stored.available_lamports == calculated_available
            and stored.reserved_lamports == calculated_reserved
            and stored.paid_lamports == calculated_paid
            and stored.retained_lamports == calculated_retained
            and stored.total_accounted_lamports == stored.funded_lamports
        )
        return ReconciliationResult(
            stored=stored,
            calculated_funded_lamports=calculated_funded,
            calculated_available_lamports=calculated_available,
            calculated_reserved_lamports=calculated_reserved,
            calculated_paid_lamports=calculated_paid,
            calculated_retained_lamports=calculated_retained,
            healthy=healthy,
        )

    def assert_reconciled(self) -> ReconciliationResult:
        result = self.reconcile()
        if not result.healthy:
            raise LedgerInvariantError("Virtual Ledger reconciliation failed")
        return result
