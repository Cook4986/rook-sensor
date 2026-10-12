# Rook execution plan

Approved October 12, 2026 (workspace DECISIONS D8/D11 amendments and D16).
`begin` reads this plan and resumes the next unblocked step recorded in WORKSTATE.
This file owns scope and acceptance checks; WORKSTATE owns completion status.

## 1. Recover the archive as a usable resource

1. Make sync status authoritative on local disk. Surface failed listings, transfers,
   copies and status writes; return failure rather than printing a false success.
   Keep the Dropbox breadcrumb as a secondary copy. Reinstall the launchd script.
2. Build a local SQLite catalog of all five source folders, staging mirrors, cached
   verdicts and review manifests. Inventory paths/metadata before reading image bytes.
   Preserve provenance and mark missing sources explicitly. Never mutate source images.
3. Export a review queue: unresolved existing candidates first, then representative
   unclassified/processed images, rare classes and disagreements. Spread selection
   across dates and approximate capture groups; distinguish sampling groups from
   confirmed independent sightings. Neither a folder nor a VLM score proves truth.
4. Offer an owner-facing archive browser with filters, source images and explicit
   approve/correct/reject/uncertain actions. Reuse the review ledger in v2 later.

**Acceptance:** forced copy failure yields nonzero exit and local failed status; a
repeat successful sync is idempotent. Catalog reruns do not inflate counts, missing
metadata is reported, duplicate filenames retain all locations, and rejected cache
records do not reappear as pending suggestions. Produce a real first review batch.

## 2. Make training evidence trustworthy

1. Enforce one paid labeler and conservative per-request spend reservation, including
   retries/unknown usage. Preserve the $50 lifetime ceiling and append-only ledger.
   Until tested, use cache-only operations with no network inference.
2. Record explicit human decisions per proposed box, tied to source and label hashes.
   The existing main-gallery keep-in-place workflow is not sufficient provenance.
   Relabel a wrong subclass to its real generic object when appropriate; do not infer
   background merely because a proposed label was rejected.
3. Build immutable approved snapshots on local disk. Reconcile existing review work;
   never discard it or silently approve old boxes. Validate raw images, class IDs,
   label geometry and completeness. Group duplicates/visits before splitting.
4. Trainer preflight rejects Dropbox-resolved paths, missing approvals and insufficient
   enabled-class coverage. Pin a reviewed local baseline/test set, including generic
   classes. Add resumable runs and manifests from the selected dataset, not defaults.
5. Require model-card/hash/expected classes at deploy; evaluate exported NCNN, verify
   a fresh inference after restart and test rollback.

**Acceptance:** unreviewed/tampered labels and missing-class validation fail before
training; same-visit images never cross splits; first release must compare the stock
baseline. A failed/missing card or stale loaded log cannot certify deployment.

## 3. Deliver the first private v2 slice (independent of model readiness)

1. Define versioned events with IDs, device, captured/received times, generic boxes,
   optional subclass/source/confidence, image reference and model identity.
2. Add a bounded durable local outbox and worker, isolated from detection and Slack.
   Ingest over outbound HTTPS using a revocable device-scoped credential. Deduplicate
   retries by device/event ID. No public inbound port on the Pi.
3. Next.js + Supabase: pre-provision the owner, disable public registration, enforce
   owner authorization/RLS on all data paths and keep images private. The bounded
   pilot uses an owner-session-checked image endpoint (D17); any later signed URLs
   must be short-lived.
   Keep elevated server credentials out of the Pi and browser. Test denial paths.
4. Ship feed, event detail, device health and the archive review experience. Follow
   existing Media mockups; display generic/uncertain labels honestly. A van sighting
   does not establish package delivery; nearby species hints are not identification.
5. Add installable PWA + opt-in push with durable jobs, failure handling and authorized
   deep links. Test on the owner's phone. Keep Slack during the pilot; no DND-bypass
   promise. Defer live streaming, multi-user access and consumer pairing wizard.

**Acceptance:** a real event/private image appears on the owner's phone; signed-out
and non-owner reads, guessed image URLs and revoked device tokens fail; offline queue
survives restart and replay causes no duplicate; push opens the correct event.
Prepare locally until hosting account, owner identity and deployment budget are known.

## 4. Improve recognition in small releases

1. Start with USPS and trash truck; mine UPS/FedEx/Amazon deliberately. Prototype crop
   classification off the live alert path and compare against the existing detector
   fine-tune before committing to an additional runtime model.
2. Collect selected native-resolution raw frames/crops with storage limits and event
   IDs. Avoid the global ten-minute gate hiding rare arrivals. Preserve accepted camera
   placement; do not reopen the physical glare fix.
3. Aim initially for 50–100 independent sightings per pilot class, with 15–20 held-out
   sightings spread across days/conditions. These are acquisition targets, not proof
   of accuracy. Use licensed external data for training if helpful; evaluate locally.
4. Run a short plumbing experiment, time a real epoch, then select a bounded run.
   Train/evaluate/export at intended deployment settings. Report event precision,
   recall, false alerts, base regressions and uncertainty; enable only validated classes.
5. Extend wildlife from real reviewed sightings, flora from stationary regions and
   scheduled observations, and phenomena from visual/temporal evidence. Weather API
   context stays separate. Append new detector IDs from 110; flood=107 stays retired.

**Acceptance:** a versioned model improves its declared enabled classes on independent
local data, preserves generic behavior, meets measured Pi latency/thermal limits,
and can roll back. Data availability determines timing; epochs alone do not.

## Session execution

At `begin`: read fresh WORKSTATE, select the next unmet acceptance check, verify its
prerequisites, and continue authorized work. Human review and account inputs can block
their dependent steps; continue other steps. At `wrap`: record concrete results and
the exact next command/action, commit/push meaningful code, and keep raw data out of git.

Do not rerun paid full-archive labeling as a default startup action. Do not silently
replace uncertainty with an approval or treat a metadata inventory as image validation.
