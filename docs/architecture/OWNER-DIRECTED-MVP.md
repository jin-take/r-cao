# Owner-Directed MVP Architecture

```text
Owner Console (Next.js)
        │ read model / command request
        ▼
Python Control Plane
  ├─ Actor Context / Owner Policy
  ├─ OwnerDirectedStore (Phase 1 reference)
  ├─ Task Workflow
  ├─ Review / Audit
  ├─ Virtual Reward Ledger
  └─ Append-only Audit Log
        │ future repository adapter
        ▼
PostgreSQL MVP schema (db/schema.sql)
```

## Boundary

LLMやNext.jsの入力は提案またはCommandです。Policy Engineの判定を通過しない
状態変更は実行しません。Python側の`OwnerDirectedStore`は、PostgreSQLへ移行する
前の実行可能なリファレンスで、Task、Sub Task、Review、Audit、Evaluation、
Reward、Approval、Proposal、External Action、Audit Logを保持します。

## Persistence contract

`db/schema.sql`にはMVP用のEnumと次のテーブルを定義しています。

`owners`、`mvp_agents`、`agent_authorities`、`agent_restrictions`、`mvp_tasks`、
`mvp_sub_tasks`、`task_artifacts`、`mvp_reviews`、`mvp_audits`、`owner_evaluations`、
`reward_budgets`、`reward_allocations`、`reward_ledger`、`approval_requests`、
`board_proposals`、`external_action_requests`、`policy_decisions`、
`mvp_audit_logs`

Agent間Reward Transfer用のTableは存在しません。`mvp_audit_logs`にはUpdate/Delete
を拒否するTriggerを定義し、ApplicationとDatabaseの双方でAppend-onlyを守ります。

## API command surface

Read endpointsはDashboard、Agent、Task Detail、Approval、Reward、Proposal、
External Action、Auditを返します。Write endpointsはOwnerまたはTask-bound Actor
を要求し、API認証で解決したActor Contextを使います。Request本文の`role`、
`actor_id`、Task membershipは権限根拠として信頼しません。

実資産・外部影響を持つAdapterはこのArchitectureに含めません。Testnet、mainnet、
MPP、Signerへ進む場合は、別のPhase Gate、Owner承認、監査、停止条件を満たす必要が
あります。
