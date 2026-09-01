# R-CAO MPP 導入方針（MPP-00）

- Status: Design approved for implementation planning
- Related issue: [#7](https://github.com/jin-take/r-cao/issues/7)
- Parent: [#18](https://github.com/jin-take/r-cao/issues/18)
- Policy source of truth: `services/rcao/app/policy.py`
- Migration gate: [#29](https://github.com/jin-take/r-cao/issues/29)

## 1. 決定の要約

MPPは、R-CAOのAgentが登録済みの外部Serviceを利用するときの
**Service Payment**にだけ適用する。MPPはTaskの正式な発行、Agentの権限委譲、
Rewardの確定、Treasuryの資産配分、Agent間送金のプロトコルではない。

このIssueで固定するのは、実送金を有効にすることではなく、後続Issueが同じ
境界を実装するための契約である。#15以降の実装が完了し、#29のGo判定をOwnerが
明示的に行うまでは、支払・署名・ネットワーク送信はlocal simulatorまたは
Solana devnet fixtureに限定する。

| 項目 | 採用する方針 | この段階でしないこと |
|---|---|---|
| Protocol | MPP wire protocolをversioned internal adapterで包む | AgentやUIがMPP wire formatを直接解釈すること |
| SDK | #7では外部SDKを依存に追加せず、adapter interfaceを正本にする。候補SDKの採用は#11で検証・固定する | 未検証SDKの自動更新、SDKのAPIをPolicy境界にすること |
| Adapter version | `rcao-mpp-profile-v1`（内部の正規化契約） | SDKの暗黙の最新版への追随 |
| HTTP 402 | 構造化されたPayment Challengeを受け、正規化・検証してからPolicyへ渡す | Challengeの自由文だけで支払うこと |
| Environment | `LOCAL`、`SOLANA_DEVNET` | testnet、mainnet、顧客環境 |
| Payment asset | devnet SPL test tokenをService Paymentに使う。SOLはネットワーク手数料のfixtureに限る | mainnet SOL、顧客資産、Reward用Virtual Ledger |
| Signer | Control Planeが作るSigner Requestを、専用Signer境界へ渡す | Agent、Provider、Browserが秘密鍵を保持・署名すること |
| Approval | `allow`、`require_owner_approval`、`deny`の3値 | `require_owner_approval`を承認済みとして扱うこと |
| Receipt | PaymentとTask/Run/Trace/Challenge/Signer/Auditを相関する | ReceiptをRewardやLedgerの記帳証明にすること |

`rcao-mpp-profile-v1`は外部MPP仕様の代替ではない。外部仕様やSDKが変更されても、
Control Planeの内部契約、Policy、監査相関を安定させるためのadapter profileである。
外部SDKの採用とHTTP clientの実装は#11で確定する。

## 2. Phaseと環境の境界

MPPの有効性は、PolicyのPhase Gateと環境値の両方で決める。

| Phase | MPPの扱い | 実行可能な宛先 |
|---|---|---|
| `PHASE_1_OFFCHAIN` | deny | なし。Virtual Ledgerのみ |
| `DEVNET` | bounded experiment | local simulator、Solana devnet fixture |
| `TESTNET` | deny for this adoption plan | なし。別のOwner判断が必要 |
| `MAINNET` | deny | なし |

`LOCAL`は実ネットワークではなく、固定されたテスト用Serviceとdeterministic
receiptを持つシミュレータである。`SOLANA_DEVNET`を選ぶ場合も、専用のテスト資産、
テスト用Service、期限の短いfixture、明示的な停止フラグを要求する。

次のいずれかが成立したらPaymentを開始せず、`deny`としてAuditする。

- Phase、network、token、recipientの組み合わせが許可Profileにない。
- Ownerのstop controlまたはincident holdが有効である。
- `expires_at`を過ぎている、nonce/idempotencyが再利用されている、またはReceiptが既にある。
- ChallengeのTask/Run/Trace相関が欠落している、または現在のTask範囲外である。
- recipientが登録済みServiceではなくAgent、Owner、Treasury、Virtual Ledgerである。

## 3. 正規化Payment Challenge

HTTP 402のwire responseはMPP adapterが受け取り、未知のフィールドや自由文を
権限情報として採用せず、以下の正規化モデルへ変換する。#11では外部MPPの実際の
challengeをこのモデルへマッピングするテストを追加する。

```json
{
  "schema_version": "rcao-mpp-profile-v1",
  "challenge_id": "challenge-uuid",
  "service_id": "service.example.compute",
  "task_id": "task-uuid",
  "run_id": "run-uuid",
  "trace_id": "trace-uuid",
  "network": "LOCAL",
  "token": "SPL_TEST_USDC",
  "amount_units": "1250",
  "recipient": "service-account-id",
  "purpose": "SERVICE_PAYMENT",
  "idempotency_key": "payment-idempotency-key",
  "nonce": "challenge-nonce",
  "expires_at": "2026-09-01T15:00:00Z",
  "correlation_id": "corr-uuid"
}
```

規則は次のとおりとする。

1. `purpose`は唯一の値 `SERVICE_PAYMENT` とし、Reward、Treasury、Transferを表す値を受け付けない。
2. `amount_units`は正の10進整数文字列とし、浮動小数点や自由な通貨表記を受け付けない。
3. `service_id`と`recipient`は登録済みService Profileの組み合わせと一致させる。
4. `task_id`、`run_id`、`trace_id`、`correlation_id`はPaymentとAuditの必須相関情報とする。
5. 正規化済みJSONを決められたcanonical JSON（UTF-8）で直列化し、`challenge_hash`をSHA-256で保存する。
6. Challengeは支払意思の入力であり、認可ではない。Policy判定と必要なOwner承認を通過しない限りSigner Requestを作らない。
7. Challengeの再利用は`challenge_id`、`nonce`、`idempotency_key`の組み合わせで検出し、同じPaymentの結果を返すだけにする。

## 4. Payment ProfileとPolicy decision

Service Payment ProfileはOwnerが登録・変更する。Profileには少なくとも次を含める。

- `profile_id`、`version`、`service_id`、`recipient`
- 許可する`network`、`token`、最大`amount_units`
- 最大有効期間、許可するTask/Service用途、リスク分類
- `approval_mode`（自動許可、Owner承認必須、常時拒否）
- 作成者、Owner承認のAudit相関、現在の有効状態

Policyの判定は、Profile、Task scope、現在のPhase、Stop control、Paymentの再実行状態を
まとめて評価する。

| Decision | 実行条件 |
|---|---|
| `allow` | local/devnet、登録済みService、Profile内の金額・期限・用途、stop解除、再利用なし |
| `require_owner_approval` | 初回Service、高リスク、Profile変更直後、上限に近い金額、ProfileがOwner承認を要求 |
| `deny` | mainnet/customer asset、Reward/Treasury/Transfer、Agent宛、期限切れ、範囲外、stop中、直接Signer |

`require_owner_approval`は未決裁状態であり、外部送信可能を意味しない。Ownerの
明示的な承認Commandと、承認時点のChallenge/Profile/Policy snapshotが揃った場合だけ
Signer Requestを作成できる。Agentの提案やMPP challengeはOwner承認の代替にならない。

## 5. Payment、Reward、Treasuryの相互分離

MPP Paymentは外部Serviceの利用料であり、R-CAO内部の報酬計算とは別の責務である。

| 種別 | 用途 | 正本 | 実行主体 |
|---|---|---|---|
| `SERVICE_PAYMENT` | 登録済み外部Serviceの利用料 | Service Payment record / Receipt | Policy通過後のSigner境界 |
| `REWARD` | Task評価に基づくAgentへのVirtual Reward | Virtual Ledger | Ownerが明示承認、Treasuryが記帳 |
| `TREASURY` | Owner承認済みの予算・留保 | Treasury / Proposal | Owner決裁 |
| `TRANSFER` | Agent間の自由な資産移転 | なし | 常にdeny |

後続のDB実装（#15）は`mvp_service_payments`をVirtual Ledgerと別テーブルにし、
`purpose = 'SERVICE_PAYMENT'`、local/devnet限定、正の整数金額、登録済みTask/Service
参照をCHECK/FKで強制する。Service Payment tableからReward allocation、Treasury
account、Agent-to-Agent transferへ書き込むFK/APIを作らない。既存のVirtual Ledger
は従来どおり`VIRTUAL_REWARD`だけを扱う。

## 6. Signer、秘密情報、Receipt

Paymentの実行経路は次の一方向に固定する。

```text
402 Challenge
  → MPP Adapter
  → Payment Profile / Policy
  → Owner Approval（必要時）
  → Signer Request
  → local/devnet Signer Adapter
  → Payment Receipt
  → Audit / Outbox / Operations read model
```

- Agent、LLM provider、MCP tool、ブラウザ、Task payloadに秘密鍵、seed phrase、署名済みtransactionを渡さない。
- SignerはPaymentの`payment_id`、Policy snapshot、承認ID、network、recipient、amountの一致を再検証する。
- Signerを直接呼べる公開APIは作らず、Control PlaneのPolicy-bound adapterだけを依存先とする。
- Receiptには`payment_id`、`challenge_id`、`task_id`、`run_id`、`trace_id`、`correlation_id`、`signer_request_id`、`network`、`token`、`amount_units`、結果、外部signature（fixtureの場合はdeterministic ID）を保存する。
- 失敗、期限切れ、停止、鍵ローテーション、replayもAudit対象とし、外部副作用を伴わないReplayではSignerを再呼出ししない。

## 7. DB・型・テストの契約

後続Issueは次の同じ語彙を使う。実装上のenum名やmigration番号は各Issueで追加するが、
意味を変更する場合は本ADRを先に改訂する。

```text
PaymentPurpose = SERVICE_PAYMENT
PaymentNetwork = LOCAL | SOLANA_DEVNET
PaymentDecision = allow | require_owner_approval | deny
PaymentStatus = PROPOSED | APPROVAL_REQUIRED | APPROVED | SIGNER_REQUESTED
                | SUBMITTED | CONFIRMED | FAILED | EXPIRED | DENIED | STOPPED
```

最低限のDB境界は以下とする。

- Paymentは`task_id`、`run_id`、`trace_id`、`correlation_id`、`idempotency_key`、`challenge_hash`を持つ。
- `purpose`、`network`、`amount_units`、`recipient_kind`をDB CHECKで検証する。
- recipientの種別は`SERVICE`だけを許可し、`recipient_agent_id`、`sender_agent_id`を持たない。
- Paymentのidentity、目的、network、token、recipient、金額はSigner Request後に変更できない。
- 同じidempotency key、challenge ID、nonceは一つのPayment副作用に収束させる。
- Audit/Outboxの相関はTask state変更と同じtransactionで保存し、Receiptの外部送信はcommit後に行う。

テストは、正常系だけでなく次を必須とする。

1. 正規化ChallengeからPaymentを作れる。
2. missing/unknown field、目的なし、期限切れ、範囲外Task、重複Challengeを拒否できる。
3. Reward、Treasury、Transfer、Agent宛、mainnet/customer assetを型・Policy・DBの各層で拒否できる。
4. Agentからの直接Signer呼出し、承認なしの`require_owner_approval`実行、stop中の送信が成立しない。
5. 同じidempotency keyの再送が一つのPayment/Receiptにreplayされる。
6. Task/Run/Trace/Receipt/Auditを相関して検索でき、ReplayがSignerやnetwork adapterを呼ばない。

## 8. 子Issueの責務と実装順

| 順序 | Issue | 責務 | このADRとの関係 |
|---:|---|---|---|
| 1 | #15 | Service PaymentとReward/Treasury/Transferの型・DB・Policy分離 | 本文の分離契約を実装 |
| 2 | #9 | Agent Payment Profile | Service、token、limit、approval modeを永続化 |
| 3 | #14 | MPP Policy、予算、停止制御 | Decisionとstop/limitを実行可能化 |
| 4 | #13 | devnet Wallet・Signer境界 | 秘密情報をAgentから隔離 |
| 5 | #11 | MPP Client・HTTP 402 | 外部challengeを正規化 |
| 6 | #8 | Service Agent・支払受付 | recipient registrationと受領条件 |
| 7 | #12 | Payment Receipt・監査・検索 | ReceiptとOperations read model |
| 8 | #10 | Payment Session・Channel | 再利用・期限・replay境界の拡張 |
| 9 | #17 | Owner ConsoleのMPP承認・Wallet接続 | Owner操作をControl Plane経由に限定 |
| 10 | #16 | MPP統合テスト・運用手順 | local/devnet fixtureと運用検証 |

後続PRは、上表より前の境界を再実装しない。特に#11がMPP SDKを追加しても、
AgentがSignerやReward APIを呼べるようにはしない。

## 9. #29へのGo / No-Go引き渡し

### Goに必要な証拠

- local simulatorとSolana devnet fixtureが完全に分離され、デフォルトは送信なしである。
- #15〜#17、#9、#13、#14、#11、#8、#12、#10、#16の境界テストがCIで成功している。
- すべてのPaymentがTask/Run/Trace/Receipt/Auditへ相関し、idempotency/replay/expiry/stopを検証できる。
- 直接Signer、Agent間送金、Reward/Treasuryへの抜け道が型・DB・Policy・APIテストで拒否される。
- 鍵の保管・ローテーション・失効、監査保全、incident対応、Ownerの緊急停止手順がレビュー済みである。
- 対象ネットワーク、Token、Service、予算、承認者をOwnerが明示している。

### 常にNo-Goとなる条件

- mainnet、顧客資産、実運用Wallet、DeFi、Staking、Validator操作を必要とする。
- Agent、LLM、ブラウザ、外部MPP challengeが秘密鍵または署名権を得る。
- Owner承認、stop control、Audit相関、Receipt、replay防止のいずれかが欠ける。
- PaymentがVirtual Reward、Treasury、Agent間Transferを直接または間接に変更できる。

このADRの完了はGo判定ではない。#29が別途、証拠を確認して移行可否を決める。
