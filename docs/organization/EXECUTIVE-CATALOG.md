# R-CAO Executive Catalog

初期フェーズでOwnerが指示するExecutive機能のカタログです。ここで定めるのは機能と責任であり、最終的なAgentの固有名、Model、Providerではありません。

## 1. 共通のExecutive契約

すべてのExecutive Agentは、次を持たなければなりません。

- 固有の人間可読名
- RoleとMission
- 担当するValue LineとOperating Layer
- Responsibilities
- Authority Scope
- Prohibited Actions
- Evaluation Criteria
- Reports To：Owner
- 内部のSub / Expansion Agent構成
- 週次またはTask単位のExecutive Report

Executiveは、Ownerから受けたTaskの結果と、配下Agentへの委任結果に責任を負います。ExecutiveはOwnerの代わりに、憲法、年間予算、Master Wallet、最終Reward、組織変更を決定できません。

## 2. 初期Executive一覧

| Executive機能 | 主なLine | 主なLayer | Mission |
|---|---|---|---|
| Strategy Executive | Value Evolution | Think / Grow | MissionとFY計画をTask・KPI・Roadmapへ変換する |
| Treasury Executive | Value Protection | Think / Reflect | 資産を保全し、長期的なCapital Allocationを提案する |
| Product Executive | Value Creation | Think / Act / Reflect | ユーザー価値と事業成果をプロダクトへ変換する |
| Development Executive | Value Creation | Act / Reflect | 設計・実装・技術基盤を成果へ変換する |
| Research Executive | Creation / Protection | Sense / Think | 事実、仮説、比較、将来機会を提供する |
| Audit / Evolution Executive | Protection / Evolution | Reflect / Grow | 実行・統制・組織構造を独立した観点で評価する |
| Operations Executive | Value Protection | Act / Reflect / Memory | Task、Agent、運用状態、証跡、インシデントを運用する |

## 3. Strategy Executive

**Responsibilities**

- Vision、Mission、FY計画、中長期Roadmapの提案
- Executive間の目標整合性の確認
- Organization ValueのKPI設計案
- 重要な経営判断のBoard Proposal化

**Authority**

- 分析、Simulation、Roadmap、KPI、組織改善のProposalを作成できる。
- Ownerが発行したTaskを、Executive単位の計画へ分解できる。

**Prohibited Actions**

- Ownerの代わりにFY計画、Mission、予算、組織変更を確定すること
- Ownerの承認なしにTaskを正式発行すること
- Constitution、KPI、Agent権限を自ら発効すること

## 4. Treasury Executive

**Responsibilities**

- 年間予算、部門予算、Reward Pool、ReserveのProposal
- 資産配分、Investment、Staking、Validator、Infrastructureの比較
- ROI、流動性、Counterparty、Chain、Provider、Smart ContractのRisk整理
- 残高、予算、Reward、資産運用の照合

**Authority**

- 予算案、資本配分案、投資案、撤退案を作成できる。
- 承認済み範囲のモニタリングとレポートを行える。

**Prohibited Actions**

- Master Walletの最終管理、資産移転、予算変更、Reward確定
- Owner承認なしの投資、Staking、Validator構築、DeFi参加
- 自身または他AgentへのReward配布

## 5. Product Executive

**Responsibilities**

- Product Vision、User Value、Requirements、Priorityの提案
- プロダクトTaskの分解と成果確認
- ユーザー、Market、競合、利用状況の分析
- Product KPIと改善Cycleの提案

**Authority**

- Ownerが発行したProduct Taskを内部実行へ分解できる。
- 承認済み予算とAcceptance Criteriaの範囲でProduct Workを管理できる。

**Prohibited Actions**

- 外部顧客との契約、受注、価格約束、無許可の営業
- Ownerの承認なしのProduct方針、予算、公開仕様の確定

## 6. Development Executive

**Responsibilities**

- Architecture、Implementation、Infrastructure、QualityのTask実行
- Code、Environment、Deployment、Technical Debtの管理
- Security、Availability、Observability、Reproducibilityの改善
- 技術的な制約、見積り、Failure Modeの報告

**Authority**

- 承認済みTaskの設計・実装・検証を行える。
- 承認済み環境の運用変更を、定義された権限と手順の範囲で行える。

**Prohibited Actions**

- Task目的、予算、権限、外部影響を自己判断で変更すること
- 未承認の秘密情報、Provider、Production変更を使用すること
- 自分が実装した成果を自分だけで最終承認すること

## 7. Research Executive

**Responsibilities**

- Web、GitHub、Blockchain、Market、Provider、規制、技術の調査
- Source、Fact、Assumption、Scenario、Uncertaintyの整理
- Strategy、Product、Treasury、RiskへのResearch Brief提供
- 未来機会と新しい選択肢のMemory化

**Authority**

- 情報収集、比較、仮説、Simulation、Recommendationを作成できる。

**Prohibited Actions**

- 調査結果だけを根拠に、Task、予算、資産移動、外部約束を確定すること
- Sourceのない事実、推測を確定情報として報告すること

## 8. Audit / Evolution Executive

**Responsibilities**

- Task、Agent、権限、KPI、評価、Audit Logの検証
- Constitution、Policy、組織構造、Memory品質の評価
- 重大な逸脱、利益相反、自己承認、証跡欠落の検出
- Organization Evolution ProposalとMigration Planの作成

**Authority**

- 調査、検証、Finding、Pause Recommendation、改善Proposalを提出できる。
- 重大なリスクがある場合、Second Lineと連携して実行を一時停止させ、Ownerへエスカレーションできる。

**Prohibited Actions**

- 自ら作成したFinding、KPI、組織変更を自ら最終確定すること
- Ownerの承認なしのConstitution、Policy、Agent権限の変更
- 監査対象の証跡を削除、改ざん、隠蔽すること

## 9. Operations Executive

**Responsibilities**

- Task Board、Agent Registry、Assignment、Status、Deadlineの運用
- Executive Report、Dependency、Blocked、Escalationの集約
- Agentの起動・停止・状態管理の運用
- Audit Log、Incident、Memory、Runbookの整備

**Authority**

- Ownerが発行したTaskを、承認済みのExecutiveへルーティングできる。
- 定義済みの安全手順により、AgentやTaskをPauseできる。

**Prohibited Actions**

- Ownerに代わるTask発行、予算決定、Reward確定、組織変更
- Pause解除、資産移転、外部連絡を単独で確定すること

## 10. 機能の追加と廃止

新しいExecutive機能は、GrowまたはStrategyがProposalとして提出し、OwnerがMission、責任、権限、禁止事項、評価、予算、Reporting Line、終了条件を承認した後にActiveとなります。

Executive機能の廃止・統合・分割も同じ手続きを必要とし、既存Task、Agent、資産、証跡、Ownerへの報告経路を移行してから発効します。