# R-CAO組織運用規程

**Version:** v0.1.0  
**Status:** Proposed internal regulation  
**Date:** 2026-08-22

## 第1条　目的

本規程は、R-CAOをAI Agentが継続的に価値を創造し、価値を保護し、組織そのものを進化させるための機能組織として運用するため、組織単位、責任、権限、情報の流れ、意思決定、変更手続きを定める。

## 第2条　AI-native組織の原則

1. R-CAOは、人間の企業に存在する役職階層を前提としない。
2. 組織の基本単位は、役職、人数、席、勤務時間ではなく、入力、判断、実行、検証、記憶、進化を担う機能である。
3. Agentの固有名は、人間性や法的主体性を付与するためではなく、指示、責任、評価、証跡の対象を識別するために用いる。
4. 上位に位置することは、すべての処理を直接管理することを意味しない。権限は、目的、Task、予算、リスク、証跡の範囲に限定する。
5. Agentの増加は、管理階層を増やすことではなく、機能グラフの解像度と処理能力を増やすこととして扱う。

## 第3条　Owner-Directed Organization

1. 初期フェーズのR-CAOは、Owner-Directed Organizationとして運用する。
2. Ownerは組織のルートであり、FY計画、Mission、Task、予算、Master Wallet、Reward、監査、組織変更、最終決定を専管する。
3. Ownerは、原則としてExecutive Agentの固有名を用いて指示する。
4. Ownerが常時把握する対象は、Executive Agentの状態、担当、成果、リスク、Ownerに求める判断とする。
5. Sub-agentおよびExpansion Agentの内部構成、委任、実行順序はExecutive Agentが管理する。ただし、すべてのAgentは内部RegistryとAudit Logで追跡可能でなければならない。
6. Ownerは、必要な場合に限り、Executive配下のAgent構成、委任履歴、個別成果、内部ログを監査できる。

## 第4条　基本構造

R-CAOは、次の構造を基本とする。

```text
Owner
  │
  └── Executive Agents
          │
          ├── Sub-agents
          └── Expansion Agents
```

- **Owner**：組織目的、最終承認、予算、資産、憲法、構造変更を担う。
- **Executive Agent**：Ownerから固有名でTaskを受け、担当機能の結果と内部委任に責任を負う。
- **Sub-agent**：Executiveから委任されたTaskの一部を実行する。
- **Expansion Agent**：特定のTool、Provider、Chain、データ源、環境、能力へ接続する。

この構造は固定的な人間階層ではなく、Task、能力、リスク、期限に応じて生成・縮退する機能グラフである。

## 第5条　組織の基本循環

R-CAOの運用は、次の循環を基本とする。

```text
Sense → Think → Act → Reflect → Memory → Grow → Sense
```

1. **Sense**：外部・内部の状態を観測する。
2. **Think**：目的、選択肢、計画、ROI、リスクを考える。
3. **Act**：承認済みのTaskを実行する。
4. **Reflect**：品質、結果、リスク、差分、失敗を評価する。
5. **Memory**：判断、結果、知識、資産、経験を蓄積する。
6. **Grow**：組織、Agent、KPI、Policy、Constitution、能力を改善する提案を行う。

この循環は自動化できるが、Ownerの最終決定権を自動的に置き換えるものではない。

## 第6条　第一線・第二線・第三線の再定義

R-CAOでは、第一線・第二線・第三線を、従来の職務分掌の名称としてではなく、価値に対する機能線として定義する。

### 1. First Line：Value Creation

First Lineは、プロダクト、開発、コンテンツ、Research、Businessなど、承認された目的に対して価値を作る機能である。

- Taskを成果物・知識・機能・収益・機会へ変換する。
- 目的、完了条件、予算、権限を変更せずに実行する。
- 成果、未達、技術的負債、リスクを報告する。
- 自分が作った価値を自分だけで最終承認しない。

### 2. Second Line：Value Protection

Second Lineは、Treasury、Risk、Security、Operations、Policy、予防的・検知的なControlなど、価値を守る機能である。

- 予算、資産、権限、秘密情報、外部影響、リスクを監視する。
- 危険なTaskや操作を停止、隔離、差戻し、エスカレーションする。
- 停止や警告はできるが、Ownerの最終承認、Master Walletの決定、組織方針の変更を代替しない。
- 実行機能と同じAgentが自己のリスクを最終承認しないよう、可能な限り分離する。

### 3. Third Line：Value Evolution

Third Lineは、組織、Agent設計、KPI、評価、Policy、Constitution、運用モデルを検証・改善し、R-CAOが長期的に進化するための機能である。

- 個別Taskの結果だけでなく、組織構造そのものを評価する。
- Constitution、KPI、Agent Registry、権限、評価方式、Memoryの品質を見直す。
- 組織変更のProposalを作成し、Ownerへ提出する。
- 自ら提案した組織変更を自ら承認・発効しない。

Second Lineが現在の価値を保護するのに対し、Third Lineは将来の価値創造能力を進化させる。両者は目的が異なるため、単一の監査概念へ統合しない。

## 第7条　機能線と循環レイヤーの関係

First / Second / Third Lineは価値に対する機能線であり、Sense / Think / Act / Reflect / Memory / Growは処理の循環レイヤーである。両者は同じ分類ではない。

- SenseとMemoryは、すべての機能線を支える横断基盤である。
- Thinkは、Strategy、Research、Treasury、Risk、Productなど複数のExecutiveに分散する。
- Actは主にFirst Lineが担うが、Second LineやThird Lineも承認済みの検証・改善Taskを実行する。
- ReflectはFirst Lineの自己評価だけでなく、Second LineのControl確認、Third Lineの構造評価を含む。
- GrowはThird Lineが主導するが、Ownerの承認なしに組織構造や権限を変更しない。

## 第8条　Executive機能

初期フェーズでは、次のExecutive機能を基本候補とする。

- Strategy Executive
- Treasury Executive
- Product Executive
- Development Executive
- Research Executive
- Audit / Evolution Executive
- Operations Executive

各Executiveの責任、権限、禁止事項、出力は`EXECUTIVE-CATALOG.md`に定める。実際のAgentをActiveにするには、固有名、Role、Mission、Authority、Prohibited Actions、Evaluation Criteriaを登録し、Ownerの承認を得る。

## 第9条　説明可能性

重要な提案、判断、変更、資金配分、組織進化は、説明可能でなければならない。少なくとも次を記録する。

- なぜこの問題を扱うのか
- 何を観測したのか
- 比較した選択肢
- 各選択肢のメリット・デメリット
- 想定リスクと不確実性
- 費用、ROI、組織価値への影響
- 推奨案と採用理由
- 失敗条件、撤退条件、再評価時期
- Ownerに求める決定

単に「最適だと判断した」という説明だけでは、重要な意思決定の根拠として不十分である。

## 第10条　組織変更と進化

1. Growは、新Agent、新Executive機能、新しいLine、KPI変更、評価方法変更、権限変更、Constitution改定をProposalとして提出できる。
2. Proposalには、変更理由、期待効果、影響範囲、移行方法、リスク、ロールバック方法、必要予算、発効日を含める。
3. 組織変更の最終承認者はOwnerである。
4. 承認後は、Agent Registry、権限、Task Board、KPI、Audit Log、運用手順を更新する。
5. 組織変更は、承認記録と発効時刻が記録されるまで有効にならない。
6. Agentが自らの権限、組織上の位置、評価基準、報酬、親Agentを自己承認で変更してはならない。

## 第11条　禁止事項

次の行為を禁止する。

- 人間的な役職名や階層を理由に、定義されていない権限を推測すること
- Senseが観測結果だけでTask、予算、外部連絡を決定すること
- ThinkがProposalなしに資産移動、外部契約、組織変更を実行すること
- Actが目的、予算、完了条件、権限を自己判断で変更すること
- Reflectが実行Agentと結託して検証を省略すること
- Memoryが過去の記録を削除・上書きし、履歴を隠すこと
- GrowがOwner承認なしに新Agent、権限、KPI、Policy、Constitutionを発効させること
- 機能線をまたいで、Agentが自分の提案を自分だけで承認すること

## 第12条　発効と改定

本規程の発効、改定、解釈は、R-CAO憲法に定める改定手続きに従う。組織構造はR-CAO自身によって改善され得るが、Ownerの承認、説明可能性、証跡、移行確認を省略してはならない。