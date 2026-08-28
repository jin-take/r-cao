# Owner-Directed MVP Rules

本書は[R-CAO憲法](./R-CAO-CONSTITUTION.md)をアプリケーションへ落とし込む
Phase 1の補足規程です。権限を拡張する解釈は禁止し、不明な場合は資産、外部影響、
監査可能性を守る方向へ倒します。

## Owner Authority

- 正式Taskの発行者はOwnerのみ。
- TaskのReward Budget設定者はOwnerのみ。
- Final Rewardの確定者はOwnerのみ。
- Executiveの任命・停止者はOwnerのみ。
- Constitution、Policy、Board Proposalの最終判断者はOwnerのみ。
- Master Walletと年間予算の最終権限者はOwnerのみ。

## Agent Restrictions

Agentは、固有名と登録済みIdentityがなければ正式な組織行為を行えません。
自己または他Agentの権限変更、Agent間Reward移転、Reward確定、未承認External
Action、規則の抜け道、悪意ある行動、情報漏洩、証跡の改ざんを禁止します。

## Separation of duties

Taskの実行者、Reviewer、Auditor、Owner Evaluationの担当者を記録上分離します。
同一Agentが自分の成果をReviewまたはAuditし、最終Rewardまで確定する経路を作り
ません。

## Reward is post-payment evaluation

Task上のSOLは`Reward Budget`です。自動支払いや確定報酬ではありません。Owner
Evaluationは参考値の計算を許しますが、その計算結果だけでRewardを`Approved`、
`Paid`へ遷移させません。

## External Action

External ActionはOwner承認を必要とし、承認のRecipient、Channel、Purpose、Content、
回数、期限から範囲を拡大できません。MVPでは外部へ送信しません。
