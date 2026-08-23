# Reward-driven Compounding Autonomous Organization (R-CAO)

## Overview

R-CAO（Reward-driven Compounding Autonomous Organization）は、**報酬（Reward）を原動力として継続的に成長する新しい組織モデル**です。

従来の自律組織（Autonomous Organization）が「意思決定の分散化」を目的としているのに対し、R-CAOは**仕事・報酬・資産・能力・組織価値の循環（Compounding）**によって、長期的に組織を成長させることを目的としています。

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

# Phase 1 System Foundation

## Architecture decision in PR #6

The repository now separates the system into two deliberate layers:

- `services/rcao`: Python Control Plane and Agent Runtime boundary. This is the
  canonical home for constitutional policy, Task state transitions, integer
  lamport Reward calculation, Agent-to-Agent message validation, run context,
  and the searchable operations contract.
- `src`: Next.js / TypeScript Owner Console. It is a read-side UI and API
  client; it does not reimplement authority, Reward math, or Task transitions.
- PostgreSQL + pgvector: transactional system of record for Tasks, Agents,
  runs, messages, memory, virtual ledger entries, and audit logs.

OpenAI Agents SDK / Responses API are supported integration points for bounded
Agent loops, handoffs, guardrails, approvals, and tracing. Codex SDK or Codex
MCP is a supported coding-specialist provider. A local SLM can be connected via
an OpenAI-compatible vLLM or llama.cpp endpoint for low-risk classification,
summarization, and retrieval assistance. Every provider returns a proposal;
the Python Policy Engine remains the authority boundary.

Rust is not a mandatory dependency. It may be introduced later only for a
measured performance, isolation, wallet, or on-chain-program requirement. The
Phase 1 baseline is intentionally Python + TypeScript + PostgreSQL so that the
domain rules and Agent workflows remain easy to inspect and test.

See [technology selection](docs/architecture/TECHNOLOGY-SELECTION.md) and
[Phase 1 boundaries](docs/implementation/PHASE-1.md) for the full decision.

## Local development

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
- `/tasks` — read-only Task Board
- `/operations` — searchable operations/read-model prototype

## Safety boundary

Phase 1 uses a virtual SOL ledger only. Owner approval is required for formal
Task issuance, final acceptance, Treasury decisions, and any policy-changing
operation. Agent messages are proposals or requests and must carry the
relevant `task_id`; `run_id`, `conversation_id`, `trace_id`, evidence references,
and idempotency keys preserve the audit chain. Direct Agent-to-Agent asset or
Reward transfers are rejected by the message validator.


- [Issue #5: Phase 1 オフチェーン組織シミュレーター基盤](https://github.com/jin-take/r-cao/issues/5)

Implements the foundation for #5.
