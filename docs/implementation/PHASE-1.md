# Phase 1 — Off-chain Organization Simulator

## 目的

R-CAO憲法と組織運用規程を、実資産を扱わない安全な実行環境へ落とし込む。Phase 1ではPostgreSQLを記録系とし、SOLはすべて仮想内部台帳で表現する。

## 実装境界

| 領域 | Phase 1 | 後続Phase |
|---|---|---|
| Task | 登録、割当、状態、受入条件 | 外部Task受付 |
| Agent | 固有名、Role、能力Hash、評価 | 動的生成、モデルルーティング |
| Reward | lamports単位の仮想台帳 | Devnet記録、本番送金 |
| Treasury | 提案、ROI、Risk、Owner決裁 | Multisig、Policy Program |
| Audit | before/after、Evidence Hash | オンチェーンHashアンカー |
| Validator | 対象外 | Validator Lab |

## 権限

- Ownerのみが正式Taskを発行する。
- Ownerのみが成果物を最終受入・却下する。
- Treasury Agentは提案できるが、承認・却下はOwnerのみが行う。
- Reward記帳はTreasuryまたはOwnerが行い、Agent間の直接送付は認めない。
- 重要操作は同一トランザクション内でAuditLogへ記録する。
- Phase 1のUIには未認証の書き込み経路を設けない。

## 基本フロー

1. Ownerが受入条件・期限・Reward上限を含むTaskを発行する。
2. ManagerがResearcher、Builder、Reviewerへ担当を割り当てる。
3. Agentが成果物を作成し、ReviewerがQuality・Risk・Final Scoreを記録する。
4. Ownerが受入または差戻しを決定する。
5. 受入後、Treasuryが貢献度とFinal Scoreに基づく仮想Rewardを記帳する。
6. 未配賦分をTreasuryに留保し、再投資案をOwnerへ提出する。
7. AuditorがEvidence Hashと状態差分を確認する。

## Reward計算

入力値は整数lamportsで保持する。Final Score 60未満は受入対象外とし、60以上では次式で支払可能額を決める。

`payable = floor(reward_pool × final_score / 100)`

payableを各AgentのContribution Score比で配賦し、端数は最後の配賦先で調整する。残額はTreasury留保とする。係数変更はPolicy変更としてOwner承認を必要とする。

## ローカル実行

```bash
cp .env.example .env
docker compose up -d postgres
npm install
npm test
npm run dev
```

- Dashboard: `http://localhost:3000`
- Task Board: `http://localhost:3000/tasks`
- Health: `http://localhost:3000/api/health`

## Phase 1完了条件

- 1つの正式Taskを複数Agentが処理できる。
- 独立Reviewerの評価とOwnerの最終受入を記録できる。
- 仮想SOL RewardとTreasury留保を台帳へ整合的に記録できる。
- Treasury再投資提案をOwnerが承認または却下できる。
- すべての重要な状態変更をAuditLogから再現できる。

現時点のUIは読み取り専用プロトタイプであり、ドメインPolicy、DBスキーマ、Reward計算の実装境界を先に固定している。次の実装では認証、Repository層、トランザクション境界、書き込みAPIを追加する。
