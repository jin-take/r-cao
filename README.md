# Reward-driven Compounding Autonomous Organization (R-CAO)

## Overview

R-CAO（Reward-driven Compounding Autonomous Organization）は、**報酬（Reward）を原動力として継続的に成長する新しい組織モデル**です。

従来の自律組織（Autonomous Organization）が「意思決定の分散化」を目的としているのに対し、R-CAOは **仕事・報酬・資産・能力・組織価値の循環（Compounding）** によって、長期的に組織を成長させることを目的としています。

組織はタスクを遂行することでRewardを獲得し、そのRewardをTreasuryで管理します。そして、ステーキング（Staking）、DeFi運用、アービトラージ、インフラ投資、Validator運営、プロダクト開発などへ戦略的に再投資することで、組織全体の価値を継続的に向上させます。

R-CAOが目指しているのは、「AIエージェントを作ること」ではありません。

**"組織そのものを進化させること"** が目的です。



# Vision

> **Building organizations that continuously grow through autonomous work, strategic capital allocation, and long-term compounding.**

**自律的な業務遂行、戦略的な資本配分（Capital Allocation）、そして長期的な複利成長（Compounding）によって、継続的に進化する組織を実現する。**



# R-CAO Philosophy

私は、AIを単なる作業を自動化するツールではなく、**組織を構成する一員（Autonomous Agent）** として考える思想が原点。R-CAOでは、エージェントはタスクを実行するだけではありません。それぞれが役割（Role）を持ち、経営（Management）、財務（Treasury）、投資（Investment）、監査（Audit）、開発（Development）、レビュー（Review）などを担当し、組織全体の価値を高めるために協調して行動をすることを目指す。

重要なことは、「利益を増やすこと」ではなく、**利益をどのように未来へ再投資し、組織価値へ変換するか**

そのためR-CAOでは、短期利益ではなく、長期的なCompoundingを重視する。


### What R-CAO Redefines (& Creator Incompetent Thinking)

R-CAOでは、現実の企業活動をSoftwareとして再定義したい。以下に現状で書いてみるが、このシステムでさらに再定義をされていくことが重要になるためこれは無能なクリエイターが作り上げた妄想である。
将来的には、単なる名称というより、**プログラムとして定義される概念（Programmable Organization）** を目指したい。

| 現実世界 | R-CAO |
|----------|--------|
| 社員 | Autonomous Agent |
| 給与 | Reward |
| 銀行口座 | Wallet |
| 財務部 | Treasury |
| 経営会議 | Governance |
| 投資委員会 | Investment Agent |
| 人事評価 | Reputation Score |
| 会社資産 | Organizational Treasury |
| 予算 | Capital Allocation |

### Creator's Philosophy

R-CAOは、AIエージェントを管理するためのプロジェクトではなく、**企業そのものをSoftwareとして設計し直したい**。

そのために、仕事（Work）、報酬（Reward）、給与（Salary）、ウォレット（Wallet）、資産（Asset）、予算（Budget）、投資（Investment）、ガバナンス（Governance）など、企業活動を構成する概念を改めて定義する。さらに、それぞれのAutonomous Agentが資産運用、投資戦略、経営戦略、予算配分などを継続的にシミュレーションし、ROIやリスクを評価しながら組織全体の最適化を目指す。最終的には、コンテンツ制作、受託開発、自社プロダクトなど、実際のBusinessによって得られたRewardを、Treasuryが管理し、Staking・Validator・Infrastructure・Product Developmentなどへ再投資することで、持続的に成長する企業モデルを実現したい。

R-CAOは、Software Frameworkですらない。**"未来の企業を設計するためのOrganizational Operating Model"** であるとしたい（概念も含めて）。




# Long-term Goal

R-CAOの最終目標は、Autonomous Organizationを現実社会へ実装すること。事業活動（Business）、資産運用（Asset Management）、投資（Investment）、予算構築（Budget Planning）、ガバナンス（Governance）を統合し、人とAutonomous Agentが協調しながら、継続的に価値を創造する新しい企業モデルを実現したい。
より賢いAgentを作ることではなく、**「時間とともに賢く成長し続ける組織」** を作ることを目指す。
さらには、AIエージェントの高度化、複雑系なども含めた社会実装と共に成長を続ける **Organizational Operating Model** でありたい

# Constitution

R-CAOの初期フェーズにおける憲法、権限分掌、Agent、Task、Treasury、監査の原則を定義しています。

- [憲法文書一覧](./docs/constitution/README.md)
- [R-CAO憲法本文](./docs/constitution/R-CAO-CONSTITUTION.md)
- [Governance](./docs/constitution/GOVERNANCE.md)
- [Agent Charter](./docs/constitution/AGENT-CHARTER.md)
- [Tasks and Operations](./docs/constitution/TASKS-AND-OPERATIONS.md)
- [Treasury and Rewards](./docs/constitution/TREASURY-AND-REWARDS.md)
- [Audit and Evidence](./docs/constitution/AUDIT-AND-EVIDENCE.md)
- [Terms](./docs/constitution/TERMS.md)

# Organization Regulation

R-CAOの組織構造を、従来の人間的な役職階層ではなく、AI Agentの機能循環として定義しています。

- [R-CAO Organization Regulation](./docs/organization/README.md)
- [Issue #3：AI-nativeな組織構造と社内運用規程](https://github.com/jin-take/r-cao/issues/3)
- [関連：R-CAO Constitution PR #2](https://github.com/jin-take/r-cao/pull/2)


# Local development

```bash
cp .env.example .env
docker compose up -d postgres
npm ci
npm test
npm run typecheck
npm run build

python3 -m venv .venv
.venv/bin/pip install -e 'services/rcao[dev,postgres]'
.venv/bin/pytest -q services/rcao/tests
```

Run the Control Plane with:

```bash
.venv/bin/uvicorn app.main:app --app-dir services/rcao --reload
```

The Owner Console is available at `http://localhost:3000`. The Python health
endpoint is `http://localhost:8000/health`; the read-only operations search
contract is `GET /api/v1/operations/search`.

The local UI routes are:

- `/` — constitutional dashboard
- `/tasks` — Owner Task Board / MVP command surface
- `/operations` — searchable operations/read-model prototype

# Owner-Directed MVP

Owner-Directed MVPは、OwnerがTask・予算・最終判断を持ち、固有名を持つ
Executive Agentが配下のSub Agentを編成して仕事を進める業務サイクルです。
R-CAOの初期フェーズでは、外部からの仕事受注は禁止し、正式なTaskの発行者は
Ownerだけとします。

## Core workflow

```text
Owner Task
  → Executive Assignment
  → Planning
  → Sub Task Execution
  → Review
  → Audit
  → Owner Evaluation
  → Final Reward Allocation
  → Completed
```

Taskの状態は`DRAFT`、`APPROVED`、`PLANNING`、`IN_PROGRESS`、`REVIEW`、
`AUDIT`、`OWNER_REVIEW`、`REWORK`、`BLOCKED`、`COMPLETED`、`REJECTED`、
`CANCELLED`で管理します。ReviewとAuditはTask実行者から分離し、Auditが
`FAIL`の場合はOwner Reviewへ進めません。

## Reward BudgetとReward

Taskの`Reward Budget SOL`は、Ownerが後払い評価時に参照する上限・評価基準です。
Task完了時の自動支払いではありません。MVPでは次の値を分離して保存します。

- `Reward Budget`：Taskに設定された予算上限
- `Proposed Reward`：評価を踏まえた参考提案
- `Approved Reward`：Ownerが明示的に確定した配分
- `Paid Reward`：将来の支払経路で確定する値。MVPでは常にVirtual Ledger

Reward Statusは`Pending`、`Proposed`、`Approved`、`Paid`、`Reserved`、
`Cancelled`です。Agent間Reward Transfer、Agentによる自己Reward確定、
未承認RewardのPaid化はPolicyで拒否します。Budgetを超えるFinal Rewardは、
Ownerによる理由入力を必須とします。

## Initial Executive Agents

| Name | Role | Mission |
|---|---|---|
| Aria | Strategy Executive | FY計画と長期戦略 |
| Mira | Product Executive | Product / Contentの価値設計 |
| Theo | Engineering Executive | Design / Development / Technical Review |
| Noah | Treasury Executive | Budget / Capital Allocation / Asset Managementの提案 |
| Iris | Audit Executive | Task / Reward / Policy / Riskの監査 |
| Luca | Operations Executive | Progress / Blocker / Owner Approval Queue |

Sub AgentとExpansion AgentはExecutive配下で管理し、Owner Consoleでは通常折り
たたみます。すべてのAgentは固有名、Role、Mission、Responsibilities、Authority、
Prohibited Actions、Reports To、Status、Versionを持ちます。

## Owner Console routes

- `/dashboard`：FY Plan、Active Tasks、Approval、Budget、Audit Alert、Executive Status
- `/agents` / `/agents/[id]`：Agent Registryと折りたたみ可能なSub Agent
- `/tasks` / `/tasks/[id]`：Jira形式Task BoardとTask詳細、Review、Audit、Evaluation、Reward
- `/approvals`：Task、Reward、Proposal、External Actionの統一Approval Center
- `/rewards`：Reward Budgetと配分状態を確認するVirtual Ledger
- `/proposals`：Board ProposalとOwner最終決議
- `/audit`：追記型Audit LogとCorrelation ID
- `/settings/policies`：Application Codeで検証するPolicy Catalog

Python Control Planeには同じ境界を検証するAPIを用意しています。代表的な
エンドポイントは`/api/v1/dashboard`、`/api/v1/agents`、`/api/v1/tasks`、
`/api/v1/approvals`、`/api/v1/rewards`、`/api/v1/proposals`、
`/api/v1/external-actions`、`/api/v1/audit`です。書き込みCommandは認証済み
Actor ContextとOwner/Task membershipを通過し、Audit Logへ記録されます。

## External Action

External Actionは、Request ID、Requested By、Recipient、Channel、Purpose、
Content、Allowed Action Count、Expires At、Owner Decision、Execution Resultを
持つ申請として管理します。Owner承認がない操作、承認範囲外のRecipient・Channel・
Content・回数・期限の操作は拒否します。Owner承認後も、今回のMVPではEmail、DM、
SNS、API Write、Contractなどへの実送信・外部書き込みは行いません。

## Setup and commands

```bash
cp .env.example .env
# .envのRCAO_AUTH_SECRETを32 bytes以上のランダム値へ置き換える
docker compose up -d postgres
npm ci
python3 -m venv .venv
.venv/bin/pip install -e 'services/rcao[dev,postgres]'

# Load DATABASE_URL from the local environment before using rcao-migrate.
set -a
. ./.env
set +a

# The compose initializer uses db/schema.sql. Stamp its equivalent migration
# history before running the migration command against this existing database.
.venv/bin/rcao-migrate --directory db/migrations --baseline-version 10

npm run dev
.venv/bin/uvicorn app.main:app --app-dir services/rcao --reload
```

検証コマンドは次のとおりです。

```bash
npm run typecheck
npm test
npm run build
.venv/bin/pytest -q services/rcao/tests
```

## Current limitations

- MVPのControl Planeは移行前のインメモリStoreです。PostgreSQLのMVP schemaを
  `db/schema.sql`に定義し、永続Repositoryへの移行余地を残しています。
- UIはローカルSeedとOwner Consoleの操作境界を示すクライアント状態です。正式な
  本番操作ではPython APIを唯一のCommand入口として利用します。
- 実SOL送金、Solana Wallet、DeFi、Staking、Validator、独自Token、Customer
  Assets、Agent間送金、外部受注、外部送信、AIによるReward確定は対象外です。
- Ownerの認証はPhase 1の署名Token境界であり、外部Identity Provider、永続Identity、
  Transactional Auditは後続のRepository/Transaction実装で追加します。

## Roadmap

1. Owner-Directed MVP：業務サイクル、Policy、Audit、Virtual Reward
2. PostgreSQL Repository、Transaction、Outbox、Replay
3. Agent Runtime、A2A、Evidence、Memory
4. Owner ConsoleとAPIの完全接続
5. MPP / Solana devnet（別Gate、実資産なし）
6. Testnet・mainnet・外部活動の移行判定（Owner承認、監査、HSM/Multisig等が前提）
