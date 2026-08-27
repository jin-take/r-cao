# Owner-Directed MVP 実装仕様

## 目的

R-CAOの初期フェーズでは、Ownerが唯一の発注者・予算決裁者・最終承認者です。
本MVPは、外部活動や実資産操作を行わず、次の組織サイクルをアプリケーションで
再現可能にします。

`Owner Task → Executive Assignment → Planning → Sub Task Execution → Review → Audit → Owner Evaluation → Final Reward Allocation → Completed`

Pythonの`services/rcao/app/mvp.py`がCommand、状態遷移、権限、Auditの正本です。
Next.jsはOwner Consoleの表示・操作面であり、将来APIへ接続する場合もPolicyを
複製してはなりません。

## 権限境界

| 操作 | Owner | Executive | Reviewer / Auditor | 備考 |
|---|---|---|---|---|
| 正式Task作成・発行 | 許可 | 拒否 | 拒否 | Ownerのみ |
| Executive割当 | 許可 | 拒否 | 拒否 | Ownerが固有名で指定 |
| Sub Task分解 | 許可 | 許可 | 委任範囲のみ | 親Taskの範囲内 |
| Review提出 | 拒否 | 拒否 | Reviewer | 実行者と分離 |
| Audit提出 | 拒否 | 拒否 | Auditor | Reviewerと分離可能 |
| Owner Evaluation | 許可 | 拒否 | 拒否 | 完了判定とは分離 |
| Final Reward | 許可 | 拒否 | 拒否 | 自動確定しない |
| Board Proposal最終決議 | 許可 | 拒否 | 拒否 | 提案と決議を分離 |
| External Action承認 | 許可 | 拒否 | 拒否 | 承認範囲を固定 |
| Master Wallet操作 | 最終権限のみ | 拒否 | 拒否 | MVPでは操作経路なし |

Agentには固有のName、Role、Mission、Responsibilities、Authority、
Prohibited Actions、Reports To、Agent Type、Status、Versionを持たせます。
Ownerが直接管理するのはExecutive / Audit Agentであり、Sub AgentとExpansion
AgentはExecutive配下に所属します。

## Task Lifecycle

```text
DRAFT → APPROVED → PLANNING → IN_PROGRESS → REVIEW → AUDIT → OWNER_REVIEW → COMPLETED
                                      ↑             │             │
                                      └─ REWORK ←───┘             └─ Owner Request Changes
```

`BLOCKED`、`REJECTED`、`CANCELLED`も履歴を保持します。Auditの`FAIL`は
`OWNER_REVIEW`へ進めません。Ownerの`Request Changes`は`REWORK`に戻します。
`COMPLETED`への遷移にはOwner Evaluationを要求します。

## Reward model

### Reward Budget

Taskに設定されたSOL単位の予算上限・評価基準です。受注時の確定報酬でも、
Task完了時の自動支払いでもありません。

### Reward Allocation

Owner Evaluation、成果物、貢献、期限、Risk、Auditを踏まえて作成する配分案です。
`Proposed`は参考値であり、Ownerの明示Decisionまで確定しません。

### Reward Ledger

Ownerが確定した`Approved Reward`と、将来の支払経路で記録される`Paid Reward`を
分離します。MVPでは`Paid Reward`は常に0で、Virtual Ledgerのみを使用します。

次の操作はApplication Policyで拒否します。

- Agent間のReward、給与、資産の直接移転
- Agentによる自己または他AgentのReward確定
- Owner承認前の`Paid`化
- Budget超過配分（Ownerの理由がない場合）

## External Action

外部Actionは、実行ではなく申請・承認・範囲検証までを実装します。Requestは
Recipient、Channel、Purpose、Content、Allowed Action Count、Expires At、
Owner Decision、Execution Resultを保持します。承認後も、MVPでは送信Adapterを
持たず、`NOT_EXECUTED_MVP`として終了します。

以下の全条件を満たさなければ`ALLOW_WITH_SCOPE`になりません。

1. OwnerがApproveしている
2. TaskがExternal Actionを許可している
3. Recipient、Channel、Contentが一致している
4. 有効期限内である
5. 許可回数を超えていない

## Audit

すべてのCommandとPolicy判定はAudit Logへ追記します。各RecordはActor、Actor
Type、Action、Target Type、Target ID、Before、After、Policy Result、Reason、
Timestamp、Correlation IDを持ちます。Audit LogにはToken、秘密鍵、seed phrase、
API secretを保存しません。DB移行後もAuditは更新・削除不可のAppend-only契約を
維持します。

## 非対象（Non-goals）

実SOL送金、Solana Wallet、Staking、DeFi、Validator、独自Token、Customer Assets、
Agent間送金、外部受注、営業、外部送信、AIによるReward確定、AIによるOwner権限
代行、Constitution自動変更は実装しません。
