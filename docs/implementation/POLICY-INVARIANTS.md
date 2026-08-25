# Policy Invariants and Phase Gates

- Policy version: constitutional-policy-v1
- Status: Phase 1 baseline
- Related issue: #24
- Source of truth: services/rcao/app/policy.py

## 目的

憲法、組織規程、Task運用、Treasury規程に書かれた権限境界を、
実行可能なPolicyとテスト可能な不変条件へ変換する。Policyは「誰が何を
実行できるか」と「現在のPhaseでどの能力を有効にするか」を判定する。
Agentやモデルの出力、Owner Consoleの入力は、Policyの判定を通過しない
限り状態・台帳・権限を変更できない。

本書はPhase 1の契約である。Issue #22でAPI認証・Owner Identity・Actor Contextの
reference implementationを追加した。永続Audit transactionはIssue #30の実装対象であり、
本書のin-memory registryを本番の永続認証基盤の代替にはしない。

## 判定モデル

Policyは次の3つの判定を返す。

| 判定 | 意味 |
|---|---|
| allow | RoleとPhase Gateの両方が許可しており、Policy Engineが実行を認める |
| require_owner_approval | 提案として扱えるが、Ownerの正式決裁なしには実行できない |
| deny | 憲法または現在のPhase Gateにより拒否する。Owner承認で迂回できない |

require_owner_approvalは承認済みを表すフラグではない。認可関数は
allowだけを実行可能とし、他の判定はPolicyViolationとして拒否する。

## 憲法・実装・テスト対応表

| ID | 憲法上の不変条件 | 実装 | テスト |
|---|---|---|---|
| POL-001 | Ownerのみが正式Taskを発行 | PolicyAction.ISSUE_TASK、authorize_task_transition | test_only_owner_can_issue_task、test_owner_and_treasury_action_matrix |
| POL-002 | Ownerのみが成果物を最終受入・却下 | FINAL_ACCEPT_TASK / FINAL_REJECT_TASK | authorize_task_transitionの既存テスト、test_owner_and_treasury_action_matrix |
| POL-003 | Treasury提案の決裁はOwnerのみ | DECIDE_TREASURY、authorize_treasury_decision | test_only_owner_can_decide_treasury |
| POL-004 | Reward記帳はOwnerまたはTreasuryのみ | POST_REWARD | test_owner_and_treasury_action_matrix |
| POL-005 | AI/Agent出力はProposalでありPolicyを迂回しない | PolicyActionにモデルの直接実行権限を定義しない | 未登録Actionがdenyになるevaluate_policyの既定動作 |
| POL-006 | Agent間の直接Reward・資産移転を禁止 | DIRECT_AGENT_TRANSFERをhard deny | test_constitutional_forbidden_actions_are_hard_denied |
| POL-007 | 初期Phaseの外部Task受付・直接販売を禁止 | EXTERNAL_INTAKEをhard deny | test_constitutional_forbidden_actions_are_hard_denied |
| POL-008 | Customer assetとMainnet assetを扱わない | PhaseCapabilityに許可を与えない | test_mainnet_and_customer_assets_remain_closed |
| POL-009 | Phaseごとに有効能力を限定する | PHASE_GATES、require_phase_capability | test_phase_one_gate_is_offchain_and_virtual_only、test_devnet_gate_allows_bounded_payment_experiments |
| POL-010 | Phase 1のSOLは仮想整数lamportsのみ | VIRTUAL_LEDGERをPhase 1で許可 | test_phase_one_gate_is_offchain_and_virtual_only |
| POL-011 | Policy変更はOwner決裁付きのPRで行う | CHANGE_POLICYをOwner-onlyにする | test_policy_change_is_owner_only |

## Phase Gate

Phase Gateは能力の許可集合であり、許可されていない能力はコード上で
PolicyViolationになる。ここでの許可は「実装してよい範囲」を示すもので、
秘密鍵、外部送金先、顧客資産を正当化するものではない。

| Phase | 許可する能力 | 明示的に許可しない能力 |
|---|---|---|
| PHASE_1_OFFCHAIN | VIRTUAL_LEDGER、OWNER_TASK_INTAKE | Solana、MPP、外部受付、Customer asset、Mainnet asset、Agent間直接移転 |
| DEVNET | Phase 1の能力、SOLANA_DEVNET、MPP_DEVNET | 外部受付、Customer asset、Mainnet asset、Agent間直接移転 |
| TESTNET | Devnetの能力、SOLANA_TESTNET | 外部受付、Customer asset、Mainnet asset、Agent間直接移転 |
| MAINNET | なし。後続の憲法改定・セキュリティGateが必要 | すべて |

DEVNETとTESTNETは将来の検証用の宣言であり、このPRだけで実資産の
送金や本番運用を有効化しない。Mainnet、Customer asset、外部受付を
有効化する場合は、別Issueのセキュリティ評価、Ownerの明示決裁、憲法改定、
段階的なリリースGateを要求する。

## 変更手順

1. 憲法・規程との対応をIssueに記載する。
2. Policy versionを更新し、Policy実装と不変条件テストを同じPRに含める。
3. OwnerがPRの変更内容と監査影響を確認する。
4. CI、Policyテスト、監査レビューが通過してからMergeする。
5. Merge後に有効PhaseとPolicy versionをOperations/Audit read modelへ記録する。

Policyの変更は設定ファイルだけで済ませず、コード・テスト・文書を
同時に変更する。Owner承認がないPRは、憲法上のPolicy変更として扱わない。

## 後続Issueとの境界

- #22: API認証・Owner Identity・Actor Context（本PRのreference implementation）
- #30: PostgreSQL transactionとAuditLogの永続的な整合性
- #28: Owner approvalとSigner境界
- #29: Devnetから先へ進むための安全Gate
- #34: Provider / Agent Runtimeの実装

関連文書: docs/constitution/R-CAO-CONSTITUTION.md、docs/organization/ORGANIZATION-REGULATION.md、
docs/implementation/PHASE-1.md。
