# Owner Interface and Decisions

OwnerがR-CAOを管理するときの情報量、指示単位、Proposal、承認、報告のインターフェースを定義します。

## 1. Ownerが見る情報

Ownerは、すべてのSub-agentを個別に管理する必要はありません。Owner ViewはExecutive単位に集約します。

各Executiveについて、最低限次を表示します。

- Agentの固有名、Role、Status
- Mission、担当領域、関連Task
- 進捗、成果、未達、Blocked
- Quality、Risk、期限、Budget、Reward
- 配下Agentの要約と重要な例外
- Ownerに求めるDecision
- 次回報告、証跡、Audit Finding

詳細が必要な場合は、ExecutiveからSub-agent、Task、Execution Log、Audit Logへドリルダウンできる構造とします。

## 2. 指示の単位

Ownerは、原則として次の形式で指示します。

```text
[Executive Agentの固有名]、[目的]を達成するためのProposalと実行計画を作成してください。
```

Ownerが直接決めるのは、主に次の内容です。

- What：何を達成するか
- Why：なぜ必要か
- Priority：何を先に行うか
- Acceptance Criteria：何をもって完了とするか
- Budget / Risk Limit：使える資源と許容リスク
- Deadline：いつまでに行うか

Agentが主に考えるのは、次の内容です。

- How：どのように達成するか
- Who internally：内部でどのAgentを使うか
- Alternatives：どの選択肢を比較するか
- Evidence：何を証拠として残すか
- Monitoring：どのKPIとEventを追うか

## 3. Board Proposal

経営、資産、外部影響、組織変更、長期的な資本配分に関する事項は、Board ProposalとしてOwnerへ提出します。

| 項目 | 内容 |
|---|---|
| `proposal_id` | Proposalの識別子 |
| `issuer` | 提案したExecutiveまたはAgent |
| `objective` | 何を達成する提案か |
| `context` | 背景、観測、前提 |
| `evidence_refs` | Source、Task、Audit、Memory |
| `alternatives` | 比較した選択肢 |
| `benefits` / `costs` | メリット、コスト、機会損失 |
| `roi_or_value` | ROI、Capability、Knowledge、Trust等への影響 |
| `risks` | 金融、技術、外部、運用、規範のRisk |
| `budget_limit` | 必要な予算と上限 |
| `exit_conditions` | 撤退、停止、再評価の条件 |
| `recommendation` | 推奨案と採用理由 |
| `decision` | Approve、Reject、Request Changes、Hold |
| `owner_decided_at` | Ownerが決定した時刻 |

## 4. Ownerの決定が必要な事項

次の事項は、AgentがProposalを作成できるが、Ownerの決定なしに確定しない。

- Vision、Mission、FY計画、中長期Strategy
- 正式なTaskの発行、Priority、Acceptance Criteria
- Executiveの任命、停止、解任
- 新しいAgent、Executive機能、権限、Budgetの追加
- Master Wallet、年間予算、部門予算、Reserve、Rewardの決定
- Investment、Staking、Validator、DeFi、外部Providerの実行
- 外部受注、契約、価格、納期、公開声明、承認済み相手への連絡
- Constitution、Policy、KPI、評価方法、組織構造の変更
- Reward、Salary、Bonus、費用精算の確定
- 高リスクTask、例外、Pause解除、Incidentの最終対応

## 5. Decisionの種類

Ownerの決定は、次の4種類のいずれかとして記録します。

- **Approve**：提案された範囲で実行を許可する。
- **Reject**：実行を許可しない。
- **Request Changes**：条件、証拠、Risk、予算、計画の修正を求める。
- **Hold**：判断を保留し、追加情報または別の時点で再評価する。

決定は、Scope、Budget、Risk Limit、Deadline、Evidence Requirementsとともに記録します。

## 6. 報告サイクル

Executiveは、Ownerへ次の形式で報告します。

```text
Executive Report
  1. Current State
  2. Objective and Acceptance Criteria
  3. Progress and Deliverables
  4. Quality and Risk
  5. Budget and Reward
  6. Blocked and Dependencies
  7. Decisions Required from Owner
  8. Evidence and Audit Findings
  9. Next Action
```

Sub-agentの活動はExecutive Reportへ要約しますが、重要なリスク、資産操作、権限逸脱、Owner判断に影響する事象は個別のAudit LogとEvidenceを残します。

## 7. Explainabilityの最低基準

重要なDecisionでは、Agentは結論だけを提出してはなりません。次の順序で説明します。

1. 観測した事実
2. 前提と不確実性
3. 選択肢
4. メリットとデメリット
5. Riskと影響範囲
6. ROIまたは長期組織価値への影響
7. 推奨案と採用理由
8. 失敗条件、撤退条件、再評価時期
9. Ownerへの質問または承認依頼

## 8. Owner不在・判断不能時

Ownerの判断が必要な事項を、Agentが推測で承認済みとして扱ってはなりません。判断不能時は、Taskを `Blocked` または `Hold` とし、必要な情報、期限、Risk、停止条件を記録します。

安全確保のための可逆的なPause、隔離、アクセス遮断は、承認済みRunbookの範囲内で実行できます。ただし、再開、資産移転、外部約束、組織変更はOwnerの決定を必要とします。