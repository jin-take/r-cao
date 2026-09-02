# Agent Registry・Capability・委任境界

R-CAOのAgentは、名前だけで実行主体として扱わない。`mvp_agents`を正本とし、
canonical identity、Role、組織上の線、Capability hash、Provider、Prompt version、
Tool allowlist、Network scope、Budget scope、Risk scope、Statusを一つのRegistryで
管理する。

## 実行前検証

AgentがRun、Message、Task assignment、Paymentに参加する前に、次を検証する。

1. Registryに存在し、Statusが`ACTIVE`である。
2. Agentの登録期限が切れていない。
3. Task-boundな操作なら、対象Taskのactive membershipがある。
4. 必要なCapability、Tool、Networkがallowlistの範囲内である。
5. 要求額がAgentのBudget scopeを超えていない。
6. 委任を使う場合、親子Agent、Task、Action、期限、Budgetを検証する。

未登録、停止、期限切れ、membership不在、scope外の操作は拒否する。空のToolまたは
Network allowlistは「全許可」ではなく「許可なし」として扱う。

## Registry変更

Agentの登録、Status、Task membership、委任範囲の変更はOwner-only commandとする。
変更履歴を`mvp_agent_change_history`へ保存し、同一transactionでAuditイベントへ記録
する。Agent自身が自分または他AgentのRole、Authority、Capability、Budgetを変更する
APIは作成しない。

## Payment Profile

MPP用のPayment Profileは`mvp_agent_payment_profiles`に分離する。Profileが存在する
ことは支払権限を意味しない。Profileは`agent_id`、`service_id`、`recipient`、
`network/cluster`、Token/Mint/Programのallowlist、`SERVICE_PAYMENT` purpose、risk
level、1回・Task・日次の`amount_units`上限、承認モード、期限、停止状態、
`version`を持つ。`wallet_id`と`public_key`は参照用の公開識別子であり、秘密鍵、seed、
署名素材は保存しない。

Profileの作成・変更・停止・rotationはOwner-only commandとし、各変更を
`mvp_agent_payment_profile_versions`のappend-only snapshotとAuditへ記録する。
更新はexpected versionを要求し、allowlist、network、上限、期限の拡張には明示的な
Owner approvalを要求する。既存Paymentは`profile_id`と`profile_version`をsnapshot
参照として保持し、Profile変更によって過去のPaymentの条件を変えない。

未登録・停止・期限切れ・rotation中のProfile、allowlist外のService/Recipient/
Token/Program、上限超過はPolicy入力として拒否する。Signerや資産移転の権限は別の
境界で管理し、Profile単体からPaymentを実行できるAPIは持たない。
