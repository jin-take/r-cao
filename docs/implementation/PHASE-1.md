# Phase 1 — Off-chain Control Plane and Owner Console

## 目的

R-CAO憲法と組織運用規程を、実資産を扱わない安全な実行環境へ落とし込む。
Phase 1では、Python Control Planeを唯一のPolicy・Task・Reward・Agent通信の
実行境界とし、Next.js / TypeScriptはOwner向けのread-side consoleとする。
SOLはすべて整数lamportsの仮想内部台帳で表現する。

## 技術選定

| 領域 | 採用 | 役割 |
|---|---|---|
| Owner Console | Next.js / TypeScript | Dashboard、Task Board、Operations検索UI |
| Control Plane | Python 3.12 / FastAPI / Pydantic | Policy、状態遷移、Reward計算、Message検証、Run境界 |
| AI provider | OpenAI Agents SDK / Responses API | 任意のAgent loop、handoff、guardrail、tracing |
| Coding provider | Codex SDK / Codex MCP | coding specialistの提案・実行境界 |
| Local model | vLLM / llama.cpp互換SLM | 低リスクな検索補助、分類、要約 |
| 永続化 | PostgreSQL + pgvector | Task、Agent、Run、Message、Memory、Ledger、Audit検索 |
| Systems language | Phase 1では未採用 | Rustは測定された必要性が出た場合だけ後続導入 |

Rustは必須ではない。Policyやドメイン計算を別言語に二重実装せず、まず
Pythonの可読性・検証性とPostgreSQLのトランザクション境界を優先する。
将来Rustを採用する場合も、性能・隔離・Wallet・オンチェーンプログラムなど
具体的な根拠を持つadapterに限定し、Policyの正本はControl Planeに残す。

## 実装境界

| 領域 | Phase 1 | 後続Phase |
|---|---|---|
| Task | Owner発行、割当、状態、受入条件 | 外部Task受付、動的分解 |
| Agent | 固有名、Role、能力Hash、評価 | 動的生成、モデルルーティングの自動最適化 |
| Agent Run | provider/model/prompt/tool allow-list、run_id、trace_id | 長期実行、再試行、分散worker |
| Agent通信 | task_id必須のMessage envelope、Delegation、Handoff、Evidence | 暗号署名、分散transport |
| Reward | lamports単位の仮想台帳、Pythonで計算 | Devnet記録、本番送金 |
| Treasury | 提案、ROI、Risk、Owner決裁 | Multisig、Policy Program |
| Memory/Search | PostgreSQL read model、pgvectorの保存境界、検索契約 | embedding評価、HNSW index、長期記憶の昇格審査 |
| Audit | before/after、Evidence、Task/Run/Messageの相関 | オンチェーンHashアンカー |
| Validator | 対象外 | Validator Lab |

## 権限

- Ownerのみが正式Taskを発行する。
- Ownerのみが成果物を最終受入・却下する。
- Treasury Agentは提案できるが、承認・却下はOwnerのみが行う。
- Reward記帳はTreasuryまたはOwnerが行い、Agent間の直接送付は認めない。
- Agentのモデル出力はProposalであり、Policy Engineを通さず状態や台帳を変更できない。
- Delegationは権限を狭めるためのもので、Owner権限を再委譲できない。
- 重要操作は同一トランザクション境界でAuditLogへ記録する。
- Phase 1のUIには未認証の書き込み経路を設けない。

## Agent間コミュニケーションの定義

Formal Messageは次のEnvelopeを持つ。

| フィールド | 目的 |
|---|---|
| `message_id` / `idempotency_key` | 重複配信と再実行を安全に扱う |
| `task_id` | どのFormal Taskに属するかを必ず追跡する |
| `run_id` / `trace_id` | どのAgent実行・観測トレースから出たかを追跡する |
| `conversation_id` / `parent_message_id` | Handoff、Reply、Escalationの因果関係を保つ |
| `message_type` | Command、Delegation、Request、Review、Evidence等を区別する |
| `authority_context` | Delegation scope、budget、risk、期限を記録する |
| `payload` | 提案内容。権限そのものは含めない |
| `evidence_refs` | Artifact、ログ、テスト、Memoryへの参照 |
| `expires_at` / `status` | 期限切れ・失効・処理状態を明示する |

`BLOCK`や`ESCALATION`を含むFormal Messageは`task_id`に紐づける。Ownerの
`OWNER_DECISION`だけが正式な決裁であり、Agent間のメッセージはCommandに
見えてもPolicyの代替にならない。Message payloadに直接送金・Wallet指定・
Reward移転を表すキーがあれば拒否する。

## SLMと検索可能な管理画面

SLMは、検索候補生成、分類、要約、Evidenceの整理など低リスクな仕事に
利用できる。vLLMまたはllama.cppのOpenAI互換endpointを経由するが、SLMが
Task発行、Owner決裁、Reward記帳、Agent生成、外部送信を直接実行することは
できない。OpenAI/Codex/SLMは同じAgentRuntime契約に対するproviderである。

Operations画面は次のread modelを横断検索する。

- TaskとTask状態
- Agent Run（provider、model、prompt version、status、token/latency）
- Agent Message（`task_id`、`run_id`、`trace_id`、sender/recipient、type）
- Memory（本文、embedding model、source Task/Run）
- AuditLog（before/after、evidence、decision）

Phase 1の`/operations`はUIのread-only prototypeであり、検索契約はPythonの
`/api/v1/operations/search`とPostgreSQL/pgvector schemaで固定する。実DBの
Repository接続、認証、全文/ベクトルindexの運用は次の実装境界とする。

## 基本フロー

1. Ownerが受入条件・期限・Reward上限を含むTaskを発行する。
2. ManagerがResearcher、Builder、Reviewerへ担当を割り当てる。
3. Agent Runtimeが`task_id`付きのRunを実行し、提案・Evidence・Messageを記録する。
4. ReviewerがQuality・Risk・Final Scoreを記録する。Contributor本人の自己レビューは禁止する。
5. Ownerが受入または差戻しを決定する。
6. 受入後、Treasuryが貢献度とFinal Scoreに基づく仮想Rewardを記帳する。
7. 未配賦分をTreasuryに留保し、再投資案をOwnerへ提出する。
8. AuditorがEvidence HashとTask/Run/Messageの状態差分を確認する。

## Reward計算

入力値は整数lamportsで保持する。Final Score 60未満は受入対象外とし、60以上では次式で支払可能額を決める。

`payable = floor(reward_pool × final_score / 100)`

payableを各AgentのContribution Score比で配賦し、端数は最後の配賦先で調整する。残額はTreasury留保とする。係数変更はPolicy変更としてOwner承認を必要とする。

計算は`services/rcao/app/reward.py`のPython実装を正本とし、TypeScript側に
同じ計算器を持たせない。

## ローカル実行

```bash
cp .env.example .env
docker compose up -d postgres
npm ci
npm test
npm run typecheck
npm run build

python3 -m venv .venv
.venv/bin/pip install -e 'services/rcao[dev]'
.venv/bin/pytest -q services/rcao/tests
.venv/bin/uvicorn app.main:app --app-dir services/rcao --reload
```

- Dashboard: `http://localhost:3000`
- Task Board: `http://localhost:3000/tasks`
- Operations: `http://localhost:3000/operations`
- Python Health: `http://localhost:8000/health`
- Operations API: `http://localhost:8000/api/v1/operations/search`

## Phase 1完了条件

- 1つの正式Taskを複数Agentが処理できる。
- 独立Reviewerの評価とOwnerの最終受入を記録できる。
- 仮想SOL RewardとTreasury留保を台帳へ整合的に記録できる。
- `task_id`・`run_id`・`trace_id`付きのAgent通信を監査できる。
- Treasury再投資提案をOwnerが承認または却下できる。
- Operations read modelからTask、Run、Message、Memory、Auditを検索できる。
- すべての重要な状態変更をAuditLogから再現できる。

現時点のUIと検索adapterは読み取り専用プロトタイプである。認証、Repository層、
DBトランザクション、Provider実接続、書き込みAPIは、Policy契約を保ったまま
次のPRで追加する。
