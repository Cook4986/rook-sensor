# Paid labeling accounting

`llm_autolabel.py` defaults to cached verdicts with **no VLM requests**. It can still
run the teacher and write unreviewed dataset proposals; it is not a training approval
workflow. `--dry-run` suppresses dataset files, not paid calls explicitly enabled
with `--allow-paid`, nor cache/accounting writes. Use `archive_catalog.py` for a
metadata-only operation without teacher inference.

## Before any paid run

Use one designated labeling Mac. Stop legacy/orphaned labelers first. File locks are
local-host coordination, **not distributed Dropbox locks**. Do not run another copy
on another machine. Move authority only after stopping the old host and copying its
complete latest ledger; never seed two active hosts from an old snapshot.

The existing `archive/autolabel/spend_ledger.jsonl` remains an untouched historical
source. Initialize `~/rook-local/autolabel/spend_ledger.jsonl` from that full history
once, using `spend_guard.initialize_ledger(legacy, destination)`. It validates every
record, uses exclusive creation and refuses to overwrite. Missing, empty, truncated
or malformed accounting stops paid work; never recreate it with a zero balance.
Preserve/back up the local ledger after runs. Unlike the catalog database, it is
irreplaceable accounting. Operational activation status belongs in WORKSTATE.

Supply `--spend-policy /local/path/policy.json`. Its required fields are:

- `endpoint`: exact HTTPS API base matching `LLM_API_BASE`.
- `verified_at` and `source`: date and authoritative evidence for the policy.
- `models`: one entry for each exact B1/B2 model identifier. Each entry specifies
  positive `input_usd_per_million`, `output_usd_per_million`, `max_input_tokens` and
  `max_output_tokens` (the latter two are positive integers).

Verify current provider pricing and hard request bounds, including image tokens,
reasoning tokens and any additional fees, before writing a production policy. Use
an actual provider upper bound for input tokens, **not average observed usage**.
The output bound is also sent as `max_tokens`; ensure the provider honors it for all
billed output. Models with incompatible caps, hidden fees or unbounded billing are
not supported by this guard. Old `LLM_PRICE_*` environment values are no longer used.
No guessed production pricing example is supplied.

## Request lifecycle

The run holds an exclusive lock on its existing local ledger before model loading.
For every HTTP attempt, including 429, server-error and malformed-response retries:

1. Compute the model's full configured input/output upper-bound cost using Decimal.
2. Refuse the request if that reservation would exceed the lifetime ceiling.
3. Append and fsync the reservation **before** sending the request.
4. Append observed usage, or an unknown-usage record, after the response/failure.

Reservations remain charged against available capacity even when reported usage is
smaller or unavailable. There are no automatic refunds. Crashes therefore leave a
conservative reservation instead of unrecorded spend. This may stop a run before its
actual bill reaches the ceiling; reconcile against provider billing separately.
Usage beyond a configured token bound records the excess and blocks future runs.
A mismatched policy, absent prices, corrupt ledger or write failure stops paid work.
`--max-usd` may lower the ceiling but cannot raise it above $50.

The ceiling covers this labeler's **accounted historical spend plus conservative
reservations**, conditional on verified bounds. It cannot control unrelated account
usage, provider billing changes or another machine bypassing the designated host.
`reserved_budget_usd` and `http_attempts` in the run manifest are deliberately distinct
from observed token totals and the number of unique crops/frames attempted.

## Validation

Tests use synthetic clients and temporary ledgers only. They exercise concurrent
process exclusion, abrupt process death, retries/timeouts, full-budget rejection,
malformed history, missing pricing, exceeded bounds, fsync/write failure and default
CLI behavior. They do not call a provider or certify a particular pricing policy.
