# R-CAO Signer Boundary（MPP-02）

- Status: implemented for `LOCAL` and `SOLANA_DEVNET` fixtures
- Related issue: [#13](https://github.com/jin-take/r-cao/issues/13)
- Policy dependency: [#14](https://github.com/jin-take/r-cao/issues/14)
- Migration: `0016_signer_boundary.sql`
- Code: `services/rcao/app/signer.py`

## 責務

SignerはMPP Service Paymentのための、秘密情報を隔離した実行境界である。
Control PlaneとAgentは秘密鍵・seed phrase・署名済みtransactionを受け取らず、
Policyが発行した短命の`MppSignerAuthorization`と、検証済みの
`SignerRequest`だけをGatewayへ渡す。

Signerは次の用途には使わない。

- Agent-to-Agent送金、Virtual Reward、Treasury操作
- testnet、mainnet、顧客資産、任意Programの実行
- 自由形式のモデル出力を支払コマンドとして解釈すること

## 境界の流れ

```text
MPP Policy ALLOW
  → short-lived MppSignerAuthorization
  → PolicyBoundSignerGateway
  → SignerRequest validation / stop / budget revalidation
  → isolated EncryptedKeyStore decrypt（Signer内部のみ）
  → Ed25519 sign
  → LOCAL deterministic transport または Solana devnet RPC
  → SignerResult / SignerReceipt / Audit
```

`Signer.execute`、`Signer.sign`、`Signer.submit`、`Signer.sign_and_submit`は、
Gatewayを経由しない直接呼出しを常に拒否する。HTTP公開ルートは作らない。
RPC実装は`api.devnet.solana.com`またはloopbackだけを受け付け、mainnet/testnet
のURLを構成段階で拒否する。

## Requestの再検証

`SignerRequest`はextra fieldを拒否し、次を同時に検証する。

- Policy authorization ID/hash、Policy decision、Payment IDの一致
- Task/Run/Trace/Correlation、Challenge、nonce、idempotencyの存在
- WalletのAgent、public key、network、cluster、rotation versionの一致
- `LOCAL` / `LOCAL_TEST_*` / `LOCAL_TEST_TRANSFER` または
  `SOLANA_DEVNET` / `SPL_TEST_*` / `SPL_TOKEN_TRANSFER` の組み合わせ
- Program、instruction、token、mint、recipientのallowlist
- per-payment、Task、UTC-dayの予算上限と現在の使用量
- expiry、stop control、Wallet status、Authorizationのsingle-use状態

Devnet SPL transferは、source/destination token account、mint fixture、recent
blockhashを要求する。Legacy Solana messageを組み立ててEd25519署名し、
`sendTransaction`にはbase64 transactionだけを渡す。RPCの結果は公開signature
としてReceiptに記録する。

## Key store

`EncryptedKeyStore`はAES-GCMでkey materialを暗号化する。永続化するJSONには
Walletのpublic identity、nonce、ciphertextだけが含まれ、ファイルは`0600`で作成する。
復号メソッドはSigner内部のprivate methodであり、Agent/Provider/Browser向けの
getterやPydantic fieldはない。

Wallet rotationは同じ論理Walletの`rotation_version`を順次増やし、public keyと
暗号化レコードを置き換える。古いSigner Requestはpublic key/version不一致で拒否
される。Wallet revokeとPolicy/Signer stopは送信前に評価される。

## 結果・監査・冪等性

Signerは成功、失敗、期限切れ、拒否、停止のすべてにResultとReceiptを作り、
Payment、Challenge、Task、Run、Trace、Correlationを保持する。Audit payloadは
`request_hash`、状態、signature、failure codeなどの公開情報だけで、signed bytesや
key materialを含めない。

同じidempotency keyと同じrequest hashの再送は保存済みResult/Receiptを返し、
transportを再実行しない。同じkeyに別requestを載せた場合はconflictとして拒否する。
送信失敗時はSigner側の予約をreleaseし、成功時だけconsumeする。

`mvp_signer_wallets`はpublic identity、`mvp_signer_requests`、
`mvp_signer_results`、`mvp_signer_receipts`は相関・監査用のcontrol-plane recordで
ある。Request/Result/Receiptはappend-only、Walletはstatus更新と順次rotation以外の
identity変更を拒否する。暗号化秘密情報をDBに保存する列は存在しない。

## 未実装の運用判断

この実装はlocal/devnet fixtureを再現可能にするためのものだが、mainnetや顧客資産へ
移行するGo判定ではない。外部MPP ClientとHTTP 402の実装は[#11](https://github.com/jin-take/r-cao/issues/11)、
Receipt検索・Owner Console・運用手順は後続Issueで扱い、[統合ゲート[#29]](https://github.com/jin-take/r-cao/issues/29)
が明示的に承認するまで環境境界を拡張しない。
