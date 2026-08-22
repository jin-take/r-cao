# R-CAO Organization Regulation

R-CAOの組織構造と社内運用を、AI Agentを前提とした組織規程として定義する文書群です。

## 文書の状態

- Version: `v0.1.0`
- Status: Proposed internal regulation
- Date: 2026-08-22
- Related issue: #3
- Separate PR: #4

本規程は、R-CAO憲法の下位文書として、組織をどのように構成し、情報・判断・実行・検証・記憶・進化をどのように循環させるかを定めます。

## R-CAOの組織観

R-CAOは、人間の会社にある社長、部長、課長、社員という階層をそのまま再現しません。組織を、固定された役職の上下関係ではなく、情報と責任が流れる機能グラフとして扱います。

```text
Sense → Think → Act → Reflect → Memory → Grow
  ↑                                      ↓
  └──────────────────────────────────────┘
```

OwnerはExecutive Agentを主な指示・報告単位とし、Sub-agentおよびExpansion Agentの内部構成はExecutiveに委任します。

## 文書一覧

| 文書 | 目的 |
|---|---|
| [ORGANIZATION-REGULATION.md](./ORGANIZATION-REGULATION.md) | 組織運用規程の本文 |
| [OPERATING-MODEL.md](./OPERATING-MODEL.md) | SenseからGrowまでの機能循環と三機能線 |
| [EXECUTIVE-CATALOG.md](./EXECUTIVE-CATALOG.md) | 初期Executive機能の責任・権限・禁止事項 |
| [OWNER-INTERFACE-AND-DECISIONS.md](./OWNER-INTERFACE-AND-DECISIONS.md) | Ownerへの報告、Board Proposal、承認経路 |

## 上位文書との関係

R-CAOの憲法、Task、Treasury、Auditの規程と本規程に矛盾がある場合は、上位文書を優先します。

- [R-CAO Constitution](../constitution/README.md)：憲法PR #2で追加された最高位の原則
- [R-CAO Constitution PR #2](https://github.com/jin-take/r-cao/pull/2)：憲法文書を追加する別Pull Request

本規程は、憲法を実装・運用するための組織構造を定めるものであり、Ownerの最終権限、初期フェーズの外部受注禁止、Agentの固有名、Master Walletの専管を変更しません。

## 規範用語

- **MUST / 必須**：必ず実施する。
- **MUST NOT / 禁止**：実施してはならない。
- **SHOULD / 原則**：合理的な理由がない限り実施する。
- **MAY / 許可**：承認済みの権限範囲内で実施できる。

組織構造の変更、Executive機能の追加、Agent権限の変更、KPI・評価方式の変更は、Growの提案だけでは発効しません。Ownerの承認、記録、移行確認を必要とします。