# MPP Client（#11）

- Status: implemented for `LOCAL` and `SOLANA_DEVNET` fixtures
- Related: [#11](https://github.com/jin-take/r-cao/issues/11), [#13](https://github.com/jin-take/r-cao/issues/13), [#14](https://github.com/jin-take/r-cao/issues/14)
- Code: `services/rcao/app/mpp_client.py`
- Migration: `db/migrations/0017_mpp_client_attempts.sql`

## 1. Payment flow

The Client treats an HTTP 402 response as an untrusted payment request. The
provider adapter parses it into the versioned `MppChallenge` contract, then
the Client performs the following single flow:

```text
HTTP 402
  → strict Challenge/schema validation
  → Payment Profile and Task/Run scope checks
  → MPP Policy decision
  → Owner approval when required
  → policy-bound Signer Gateway (#13)
  → public proof/receipt retry to the same provider
```

Only a successful `ALLOW` decision can create a Signer authorization. A
`REQUIRE_OWNER_APPROVAL` result is returned as `PENDING_APPROVAL`; it is never
treated as success. A `DENY`, Signer failure, or stop result has no provider
retry.

## 2. Challenge contract

`rcao-mpp-profile-v1` is a strict internal adapter contract. Unknown fields,
non-402 responses, non-JSON bodies, wrong payment method/network pairs,
non-positive or non-decimal-string amounts, unsupported purposes, forbidden
assets, internal recipients, missing correlation IDs, and expired challenges
are rejected or denied before a side effect.

`LOCAL_TEST` challenges use only `LOCAL` and local fixture tokens. `SPL_TOKEN`
challenges use only `SOLANA_DEVNET`, a test mint, the SPL Token Program, and
validated source/recipient token accounts. The Client runs only in the
`DEVNET` Phase; `PHASE_1_OFFCHAIN`, `TESTNET`, and `MAINNET` are denied.

The canonical challenge payload is hashed with SHA-256. A public
`MppPaymentProof` contains only payment identity, Signer result references,
request hash, external fixture signature, network, token, and amount. It never
contains a private key, seed phrase, or signed transaction bytes.

## 3. Policy, approval, and retry boundary

The Client compares Service, recipient, token, mint, Program, Profile version,
Task, and Run scope before calling `MppPolicyEngine`. The engine revalidates
Profile status, purpose, limits, expiry, stop controls, and the current Phase.
The Client then calls `PolicyBoundSignerGateway` exactly once and submits one
proof retry to the same registered provider. Provider receipts must match the
payment, Challenge, Signer request/result, and external signature.

Provider adapters are registered explicitly in `MppProviderRegistry`; an
unknown provider fails closed. `MockMppService` and deterministic local/devnet
transports provide repeatable tests without external network access.

## 4. Replay and public attempt history

The in-process Client binds each idempotency key to one canonical Challenge and
each `(challenge_id, nonce)` to one idempotency key. Replaying the same
Challenge returns the stored result without calling the Signer or provider;
reusing either identity for different payment data raises an idempotency
error.

`mvp_mpp_client_attempts` stores only public retry metadata and correlation
IDs (`payment_id`, `challenge_id`, `task_id`, `run_id`, `trace_id`,
`correlation_id`, Signer references, proof hash, status, and reason). It is
append-only and contains no key material or signed transaction.

## 5. Verification

The Client contract is covered by `services/rcao/tests/test_mpp_client.py`:

- successful `402 → Challenge → Policy → Signer → proof retry`;
- malformed, tampered, expired, out-of-scope, and over-limit Challenges;
- Owner approval pending/resume and denial;
- duplicate Challenge/idempotency replay without double payment;
- Signer failure, stop control, unknown provider, and phase separation;
- local and Solana devnet fixture payment paths.
