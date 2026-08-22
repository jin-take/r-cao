# R-CAO Audit and Evidence

R-CAOの判断、実行、評価、資産操作を後から検証できる状態にするための監査と証跡の原則を定義します。

## 1. 監査の目的

監査の目的は、Agentを監視することだけではありません。誰が、何を、なぜ、どの権限で、どの結果を得て、どの承認を受けたかを再現可能にすることです。

監査では、少なくとも次を確認します。

- Ownerの発行・承認が存在するか
- 実行がTask、Role、権限、予算の範囲内か
- 完了条件と成果物が一致しているか
- 品質、リスク、期限、外部影響が評価されているか
- Reward、給与、資産移転の根拠があるか
- 禁止事項、例外、権限変更が発生していないか
- 必要な証跡が改ざんされず保持されているか

## 2. Audit Logの必須項目

```json
{
  "event_id": "immutable-event-id",
  "actor": "agent-or-owner-id",
  "action": "action-name",
  "before": "previous-state-or-hash",
  "after": "new-state-or-hash",
  "hash": "evidence-hash",
  "task_id": "related-task-id",
  "decision_id": "related-decision-id",
  "approval_id": "related-approval-id",
  "created_at": "timestamp",
  "metadata": {}
}
```

最低限の要件は、`actor`、`action`、`before`、`after`、`hash`、`created_at`です。実装では、Task、Decision、Approval、Agent、Wallet、外部連絡との参照関係を持たせます。

## 3. 記録対象

| 領域 | 記録するイベント |
|---|---|
| Task | 発行、割当、委任、開始、停止、差戻し、完了、Owner承認、取消し |
| Agent | 登録、権限変更、Model / Tool変更、Active化、Suspension、Retirement |
| Review | Reviewer、品質、リスク、コメント、判定、再鑑 |
| Treasury | Proposal、予算承認、Wallet操作、資産移転、照合、Reward支払 |
| 外部連絡 | 相手、承認者、目的、内容、送信、返信、次のAction |
| Governance | Ownerの決定、例外、憲法改定、緊急停止、復旧 |

## 4. 監査の役割分担

1. 実行Agentは、自分の実行結果と証跡を提出する。
2. ReviewerまたはAudit Agentは、実行結果を独立した観点で確認する。
3. Executive Agentは、担当Task全体の説明と未解決リスクを報告する。
4. Ownerは、重要Task、資産、外部影響、例外について最終確認を行う。
5. 実行Agentが自身の成果を提出することは許可するが、自身だけで最終評価、Reward確定、Owner承認を完了させてはならない。

## 5. 監査タイミング

- **事前監査**：Task、権限、予算、外部影響、リスク、完了条件を確認する。
- **実行中監査**：重要な変更、逸脱、期限、資産、外部影響を確認する。
- **完了監査**：成果物、完了条件、品質、リスク、証跡、Reward根拠を確認する。
- **定期監査**：Agent Registry、権限、Wallet、残高、未解決Task、例外を確認する。
- **インシデント監査**：原因、影響、初動、復旧、再発防止、憲法適合性を確認する。

## 6. インシデント対応

権限逸脱、資産の不整合、証跡欠落、未承認の外部連絡、規則回避、秘密情報の露出が疑われる場合は、次の順序で対応します。

1. 可逆的な停止、隔離、アクセス遮断を行う。
2. 関連Task、Agent、Wallet、Commit、外部連絡を特定する。
3. ログ、差分、残高、承認記録を保存する。
4. Ownerへ報告し、影響範囲と暫定措置を決める。
5. 原因、再発防止、権限修正、Taskの再開または取消しを記録する。
6. Ownerが復旧、Reward、資産移転、対外説明の最終判断を行う。

インシデントを理由にAudit Logを削除してはなりません。訂正が必要な場合は、元の記録を保持し、訂正イベントを追加します。

## 7. オフチェーンとオンチェーン

R-CAOは、実装初期からすべての情報をオンチェーンへ載せることを要求しません。まずはオフチェーンで運用を安定させ、改ざん検知や承認証明に有効な情報だけをハッシュ等でオンチェーン化します。

オンチェーンに記録してはならない情報には、秘密鍵、署名情報、認証情報、不要な個人情報、未公開の機密成果物が含まれます。