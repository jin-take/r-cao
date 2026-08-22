# R-CAO Constitution

R-CAO（Reward-driven Compounding Autonomous Organization）の憲法および組織運用規程を管理する文書群です。

## 文書の状態

- Version: `v0.1.0`
- Status: Proposed baseline
- Date: 2026-08-22
- Scope: R-CAOの初期フェーズ
- Related issue: #1

このPull RequestがOwnerにより確認・承認され、`main`へ取り込まれた時点で、初期フェーズの基準文書として扱います。

## 文書の優先順位

文書・Prompt・コード・運用手順の間で矛盾がある場合、次の優先順位に従います。

1. `R-CAO-CONSTITUTION.md`（憲法本文）
2. `GOVERNANCE.md`、`AGENT-CHARTER.md`、`TASKS-AND-OPERATIONS.md`、`TREASURY-AND-REWARDS.md`、`AUDIT-AND-EVIDENCE.md`（下位規程）
3. Taskごとの承認済み仕様・運用手順
4. 実装、Prompt、Agentの内部計画

下位文書や実装は、憲法の制約を弱めたり、Ownerの最終権限を迂回したりしてはなりません。

## 文書一覧

| 文書 | 目的 |
|---|---|
| [R-CAO-CONSTITUTION.md](./R-CAO-CONSTITUTION.md) | R-CAOの最高位の原則と禁止事項 |
| [GOVERNANCE.md](./GOVERNANCE.md) | Owner、Executive、Sub-agent、監査機能の権限分掌 |
| [AGENT-CHARTER.md](./AGENT-CHARTER.md) | Agentの身分、名前、Role、委任、評価、ライフサイクル |
| [TASKS-AND-OPERATIONS.md](./TASKS-AND-OPERATIONS.md) | Taskの発行、実行、完了、外部受注制限 |
| [TREASURY-AND-REWARDS.md](./TREASURY-AND-REWARDS.md) | Master Wallet、予算、Reward、給与、再投資 |
| [AUDIT-AND-EVIDENCE.md](./AUDIT-AND-EVIDENCE.md) | 監査、Audit Log、証跡、インシデント対応 |
| [TERMS.md](./TERMS.md) | R-CAO固有用語の定義 |

## 規範用語

本文中の次の表現は、実装上の拘束力を持つものとして扱います。

- **MUST / 必須**：例外なく実施しなければならない。
- **MUST NOT / 禁止**：実施してはならない。
- **SHOULD / 原則**：合理的な理由がある場合を除き実施する。
- **MAY / 許可**：上位規程およびOwnerの承認範囲内で実施できる。

## 改定の扱い

憲法の改定は、次の記録を残したPull Requestで行います。

1. 改定理由と影響範囲をIssueに記載する。
2. 変更対象、移行方法、既存Task・資産・権限への影響を明示する。
3. Ownerによる最終承認を得る。
4. 改定後のVersion、発効日、変更履歴を更新する。
5. 実装・Prompt・運用手順が新しい憲法へ追従していることを確認する。

緊急時の一時停止は安全確保のために許可しますが、憲法そのものを黙って無効化することはできません。