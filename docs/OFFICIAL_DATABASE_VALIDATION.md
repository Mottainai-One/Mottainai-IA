# Official database validation — 2026-10-14

Scope: PR #6 in Mottainai-IA. Reference: Mottainai-Banco-Operacional develop,
revision bc3d5cfe56f60cb750a87dd7d22a7eacf90c3868.

## Removed lines

Of the original 6,261 removed lines, 5,302 were the retired IA SQL schema/data
copies (4,005 schema and 1,297 fixtures). The old operational consultation
queries were replaced with analytical queries. Their schema-specific tests were
replaced with official-contract tests. No official database table was removed
or introduced: the installed operational schema has 145 base tables including
partitions; the analytical schema has 116.

## Validation performed

- Compilation and Ruff passed for application code and changed tests.
- Full unittest discovery after Firebase integration: 409 tests, OK, two opt-in PostgreSQL checks skipped
  in this isolated unit run. The separate PostgreSQL suite passed three checks,
  including those two checks, with a non-owner runtime login.
- Thirteen live startup/HTTP/protocol checks passed: health/live/readiness,
  session listing, metrics, audit, role restriction, MCP/A2A business queries,
  company isolation, logout, revoked JWT rejection and unavailable publication.
- Seven live write checks passed: completed retry, separate receipt/disposal
  keys, inactive account replay rejection, parallel retry observed in the
  test, role mismatch, company mismatch and missing protected identity.
- Real SQL queries verified owner/manager/operator store boundaries, cancelled
  sales exclusion, exact revenue/disposal cost, and isolation of another company.
  Receipt/disposal changed only operational balances. The analytical stock
  snapshot stayed at 48; its login rejected DELETE.
- Groq-backed HTTP conversations covered stock, conversational follow-up,
  alerts, financial KPIs, prediction and an out-of-scope question. Actual
  responses and Judge evaluations were inspected, rather than treating HTTP
  success as business correctness. Correct fixture values were stock 48,
  minimum 7, difference 41, revenue 100, disposal cost 6 and two active alerts.
  The final predictive run explained the computed seven-day demand of 13 and
  retained conditional, explicitly confirmed suggestions.

## Regressions corrected during validation

- Cached writes now require an active operational account and scope keys to
  the user and operation, preventing cross-operation and cross-user replay.
- MCP/A2A bind a server-configured operational account and restore request
  identity after the call. Missing/revoked mappings fail closed.
- Forecast history ends on the analytical publication date rather than today;
  prediction responses include that date. Evidence identifies analytical sources.
- Unpublished or absent company/store analytical data fail closed instead of
  inventing zero KPIs or falling back to operational consultations.
- Missing alert priority cannot establish criticality; missing maxima remain
  unavailable. Weather does not supply unsupported percentage sales impacts.
  Predictive narration does not extrapolate the calculation to a longer period
  or infer total usable stock from a limited list of expiring batches.

## Deployment limits

The native official databases contain zero business users and zero successful
analytical publications. Live business tests used an isolated installation of
both official schemas with synthetic users/data, separate from native data.

Direct Firebase token verification is implemented. Real Firebase logins for
Estoquista, Gerente and Dono were verified against the existing firebase_uid
binding in the isolated official operational installation. All three passed
/chat and the Judge, loaded their own history, and cross-user history access
was rejected. Identity, company and role come from SQL, never client claims.

The accompanying Android change sends Firebase ID tokens directly to IA and
removes the unavailable SQL token-exchange call. Its debug APK and unit tests
built successfully in the isolated mobile worktree. The opt-in Android
instrumentation test also passed for all three staff roles: real login, IA/Chat
navigation, message submission and visible response with the expected values.
Screenshots were captured. This validates login/chat, not every other mobile feature.

The official analytical model does not publish maximum stock, alert priority,
alert descriptive fields or batch codes. The IA reports them as unavailable.
MongoDB remains conversation/RAG storage and Redis remains cache infrastructure.

Writes use an ownership-token Redis lock per company/user/operation/retry key.
Overlapping requests receive HTTP 409 and must keep the same key; completed
requests replay the cached result for the configured TTL (24 hours by default).
Acquisition uses SET NX and release compares the ownership token atomically
through redis-py's Lua implementation. The cache is checked again inside the lock.
The lock deliberately has no lease: an expired lease could let another worker
duplicate a slow PostgreSQL write. Validation/authorization failures release it
after transaction rollback; cancellation, unknown database errors, serialization
errors and failed result storage retain it for manual reconciliation. A worker
crash can therefore block that key rather than risk applying the movement twice.

For a retained key, first stop/confirm termination of the original execution and
inspect the operational inventory_movement/disposal audit trail and logs. If the
write committed, recover its original result under the same result key with the
configured TTL before removing the associated `:lock` key. Only remove a lock
without restoring a result after proving rollback/no committed movement. If the
outcome cannot be established, keep the key blocked and investigate; do not
change the client key or automatically clear locks. Redis eviction, data loss,
failover and retries after result expiry remain outside a durable exactly-once
guarantee. Use persistence and a no-eviction policy for this Redis deployment.
No SQL tables are introduced for idempotency.

The predictive engine requires a real SQL DATE snapshot_date on every nonempty
analytical evidence row, within the representable 28-day history window. Missing,
malformed and out-of-range dates fail closed before notifications or model calls;
they never substitute today's date. Empty datasets remain valid. LLM output
remains variable; the Judge rejects unsupported output.

The PR is for review. No merge or deployment was performed.

## Review regression validation (2026-10-15)

Compilation and Ruff passed for the CI scope and modified tests. Full unittest
discovery passed: 418 tests, including two skipped opt-in PostgreSQL checks.
New cases cover concurrent same-key writes, company/key isolation, cache recheck,
failed result storage, cancellation, uncertain database errors, rollback retries,
Decimal replay, HTTP 409 and malformed/missing/out-of-range publication dates.
The existing historical-publication regression still includes sales on the last
published day. Four unrelated test files retain pre-existing Ruff findings outside
the CI lint scope; they are not part of these fixes.

Against real Redis, four independent processes competed for one temporary key:
one callback executed, three received conflicts and a later replay used its cached
result. A cancelled callback retained a non-expiring reservation and rejected retry.
Temporary validation keys were removed after the workers terminated.

Against the isolated official operational/analytical databases, Estoquista, Gerente
and Dono each executed one receipt while ten concurrent duplicates were rejected.
Replay did not change the operational balance; analytical snapshots stayed
unchanged. A compensating disposal restored each test balance. The predictive
engine accepted the real analytical SQL DATE and preserved its publication date;
model/weather calls were stubbed for that check. No new live LLM quality evaluation
or mobile instrumentation run is claimed by this review validation.

## Firebase regression validation

Signature, project audience, issuer, expiration, authentication time, forged
signatures and disabled Firebase mode were tested. SQL tests verified the
Administrator/Manager/Operator mapping, inactive-account denial, missing UID
binding rejection and disregard of client-supplied role/company claims.
Certificate-service failure returns 503. Firebase token fingerprints support
local IA logout; global Firebase revocation is not queried by this verifier.

Judge evidence now separates factual constraints from presentation guidance.
A correct manager stock answer was initially rejected for date formatting;
the corrected evaluation passed without relaxing value, role or tenant checks.
