# Audit・Outbox・Replay契約

R-CAOの状態変更は、変更そのものと同じtransactionでAuditイベントと
Outboxイベントを保存する。外部API、Signer、Wallet、PaymentをDB transactionの
中で実行せず、Outboxのdelivery statusを使って後続の配信処理を管理する。

## Auditイベント

`services/rcao/app/audit.py` の `AuditEvent` が共通契約である。イベントには次を
含める。

- event version / event type
- actor、target、Task、Run、Message、Payment、Ledgerの相関情報
- before / after state
- Policy result、reason、correlation ID、transaction ID
- evidence hash、イベントhash、直前イベントhash

Auditはappend-onlyであり、DB側の既存triggerによってUPDATE/DELETEを拒否する。
新規イベントは正規化したJSONからSHA-256を計算し、token、secret、private key、
seed phrase、passwordなどは保存前にマスキングする。既存のハッシュなし記録は
履歴として保持し、新規イベントと混同しない。

## Outboxイベント

OutboxはTaskなどの状態変更を外部配信するためのdurable intentである。状態変更と
Outbox INSERTは同一transactionで行うが、配信処理はcommit後に実行する。

`PENDING → IN_FLIGHT → PUBLISHED` を基本経路とし、失敗時は `FAILED`、試行回数、
エラー、次回実行可能時刻を保存する。同じidempotency keyの再送は一つの副作用に
収束させる。

## Replay境界

`replay_audit_events` は順序付きAuditイベントから記録済みのbefore/after stateを
再構成する純粋な処理である。ハッシュ不一致、before stateの不整合、外部副作用を
伴うイベントを検出した場合は停止する。

Replayでは次を絶対に呼び出さない。

- 外部API・メール・DM・SNS
- Signer、Wallet、Solana RPC
- Payment、資産移転、MPP配信

したがってReplayは「過去に何が起きたか」を再現するためのものであり、過去の
副作用をもう一度実行する機能ではない。
