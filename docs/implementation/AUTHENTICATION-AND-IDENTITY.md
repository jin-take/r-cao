# API Authentication, Owner Identity, and Actor Context

- Policy version: constitutional-policy-v1
- Scope: Phase 1 reference implementation
- Related issue: #22
- Source of truth: services/rcao/app/auth.py

## Purpose

APIの呼び出し元が自分でOwnerやAgentのRoleを名乗ることを禁止し、
認証されたsubjectからR-CAOの正規Identityを解決する。解決したIdentityを
request-scoped Actor ContextとしてPolicy、Task、Agent通信、将来のSignerへ
渡す。

この文書は、PostgreSQLや外部Identity Providerを導入する前のPhase 1
reference implementationを定義する。認証情報の永続管理とDB transaction
は後続Issueで実装する。

## Identity model

ActorIdentityは次の情報を持つ。

| 項目 | 役割 |
|---|---|
| actor_id | R-CAO内の不変な主体ID |
| subject | 認証プロバイダまたはサービスが発行した外部主体ID |
| name | 人間が監査できる一意なAgent名 |
| role | Owner、Manager、Builder、Treasuryなどの正規Role |
| actor_type | OWNER、AGENT、SERVICE |
| phase | PHASE_1_OFFCHAIN、DEVNETなどの実行環境 |
| status | ACTIVE、SUSPENDED、REVOKED |
| task_ids | Agentが参加できるTaskの範囲 |
| capabilities | 許可された提案・実行能力 |
| identity_version | 権限変更時に失効させる世代番号 |

RoleはBearer tokenから採用しない。tokenにRoleを含めず、署名検証後に
subjectをIdentityRegistryへ照会してRoleを解決する。Agent actorにOWNER
Roleを登録することも、重複したactor_id、subject、nameを登録することも
拒否する。

## Token contract

RCAOのPhase 1 tokenは、署名対象のheader、claims、signatureから成る。

| Claim | 内容 |
|---|---|
| iss | 発行者 |
| sub | IdentityRegistryで解決するsubject |
| tid | 失効対象となるtoken ID |
| iat / exp | 発行時刻と有効期限 |
| phase | 実行環境境界 |
| iv | Identityの世代番号 |
| ver | token契約のversion |

署名はHMAC-SHA256を使用するreference implementationである。実運用では
RCAO_AUTH_SECRETをSecret Manager、KMS、HSM等から注入し、秘密値をAgent、
ブラウザ、ログ、APIレスポンスへ渡さない。

## Request flow

1. APIがBearer tokenを受け取る。
2. tokenの形式、署名、issuer、version、期限、phaseを検証する。
3. subjectをIdentityRegistryへ照会する。
4. Identityのstatus、phase、identity_versionを検証する。
5. canonical role、task membership、capabilityからActor Contextを作る。
6. Policy EngineへContextとPolicyActionを渡す。
7. 許可されない場合は実行せず、認証・認可結果をAuditへ記録する。

GET /api/v1/auth/meはActor Contextを確認するread endpointである。
POST /api/v1/auth/policy-checkは提案されたActionを実行せず、allow、
require_owner_approval、denyを返す。実際のTaskやLedgerを書き換える
command endpointは、authorize_actor_actionを通過させる。

## Security invariants

- role、actor_id、task membershipをリクエスト本文から信頼しない。
- tokenの期限切れ、明示的な失効、Identityの停止、世代変更を拒否する。
- Phaseが異なるtokenを別環境で利用できない。
- Owner以外の正式Task発行、最終受入、Treasury決裁をPolicyで拒否する。
- AgentのActionにはTask membershipを要求する。
- 生のBearer token、秘密鍵、seed phraseをAuthAuditEventへ保存しない。
- 認証失敗の詳細をAPIレスポンスへ返さず、外部へは401だけを返す。
- healthとread-only prototypeは公開可能だが、state-changing commandは
  require_actorを必須とする。

## Environment configuration

ローカルでは.envへ次の値を設定する。

- RCAO_AUTH_SECRET：32 bytes以上のランダムな秘密値
- RCAO_PHASE：初期値はPHASE_1_OFFCHAIN
- RCAO_OWNER_ID：正規Ownerのactor_id
- RCAO_OWNER_SUBJECT：認証プロバイダ上のOwner subject
- RCAO_AUTH_ISSUER：token発行者名

.env、CI secret、Secret Managerの実値はGitへコミットしない。
