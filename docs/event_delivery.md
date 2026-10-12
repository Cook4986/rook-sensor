# Device events and durable delivery

This is the local foundation for execution-plan milestone 3. The engine has an
opt-in camera adapter, disabled by default and not deployed to the Pi yet. The
receiver, owner app, private storage and account setup are subsequent work. No
network request occurs on import, capture, queue creation or queue inspection.
The delivery worker runs separately from the engine and its Slack functions.

## Version 1 observation

`app/rook_events.py` validates a closed JSON object with these fields:

- `schema_version`: integer 1; incompatible changes require a new version.
- `event_id`: canonical UUID, generated once at capture and retained for retries.
- `device_id`: provisioned stable device identifier, not a hostname lookup.
- `kind`: `detection` for nonempty detections, `motion` for an empty list. These
  are observations, not certified independent visits or proof of a delivery.
- `captured_at`: UTC timestamp ending in `Z`, recorded at frame acquisition.
- `received_at`: null on the immutable device payload. The receiver supplies its
  own clock in a separate receipt and stores it alongside the original event.
- `model`: name, format (`ncnn`, `pytorch`, `onnx`), `artifact_sha256` and
  `classes_sha256`. A custom model card is not required for stock detections.
- `detections`: at most 200 objects containing `class_id`, `label`, `confidence`,
  normalized `xyxy`, and nullable `subclass`. Optional subclass evidence contains
  its own `label`, `source`, `confidence`; it does not replace the detector label.
- `image`: null or a content-addressed reference with `sha256`, `media_type`,
  `byte_length`, `width`, `height`, and `role: raw`. No file paths or public URLs.

Device class IDs retain the existing 0–109 contract; retired flood107 is rejected.
Transport validation checks shape, bounds and hashes, not semantic correctness of
the detector's label. The producer must obtain IDs/names from the loaded model;
the receiver must check them against the registered model vocabulary. Neither an
event nor an optional subclass is human training approval.

The producer hashes the artifact before loading and after resolving the loaded
model's names. Deferred verification preserves NCNN thread setup before lazy
backend initialization. Any mismatch disables event capture, preserving the loaded
detector. Missing pre-load artifacts (including a first-run auto-download) cannot
be certified during that startup; restart with the existing local artifact.

For a single weights file the identity is its byte SHA256. For NCNN directories,
hash `canonical` of the sorted relative-file manifest: each record has `path`,
`bytes`, `sha256`. Include all regular files, require `.bin` and `.param`, reject
symlinks and changing files, and bound reads to 1,000 files/512 MiB. Do not add
runtime files inside the model directory. The ordered name list is also hashed
with `canonical`. Registration evidence is retained beside the spool in
`models/<artifact_sha>-<classes_sha>.json`, without absolute source paths.
The hosted receiver must register/validate these manifests; this is not yet built.

`canonical` is UTF-8 JSON, sorted keys, compact separators, literal Unicode, no
NaN/Infinity. The event digest covers these exact bytes. The wire envelope carries
these bytes as an `event_json` string. The receiver hashes its UTF-8 bytes directly
before parsing; it must not reserialize the parsed object to calculate the digest.
This preserves Python number formatting through a JavaScript receiver.

Each event is at most 64 KiB; an attachment is at most 2 MiB. The spool verifies
attachment length and SHA256 against its reference. It does not decode JPEGs or
prove that pixels are unannotated. The capture adapter encodes a raw frame,
provides actual dimensions, and never uses `results.plot()` or an alert preview.
The receiver must independently decode/validate JPEG content and dimensions.

## Local durability and bounds

`app/rook_outbox.py` stores canonical event bytes and optional image bytes in one
SQLite transaction on local disk. A successful `enqueue` means the commit returned
with `synchronous=FULL`. A process crash before commit cannot leave a partial event
or orphaned attachment. Power-loss guarantees still depend on the storage device
honoring flushes. Do not place the spool in Dropbox or another network/sync folder.

Defaults are 1,000 records and 256 MiB of live event/image bytes. Limits persist in
the database; reopen uses the saved values and rejects conflicting explicit limits.
The database allocation ceiling adds 8 KiB per allowed record plus 1 MiB overhead;
the DELETE journal can temporarily require roughly another database's worth of
space. Freed pages are reused. These are queue bounds, not an entire-device quota.
The database is mode600; newly created parent directories are mode700. Storage is
private to the OS account, not application-level encrypted.

When full, `OutboxFull` rejects the new item and preserves all queued work. Blocked
items count toward the same limits. There is no silent eviction or automatic
dead-letter deletion. Producer integration must surface drops/full/disk/lock errors
in health and keep detection/Slack working. Only the spool copy is removed on ack;
archive sources and human-review ledgers are never touched.

Repeated enqueue of the same device/event ID and identical bytes is a no-op while
queued. Different bytes with that identity are an error. Acknowledged IDs need
receiver-side deduplication; the local queue does not retain unbounded tombstones.

SQLite I/O is synchronous and can block or fail. The capture adapter uses a small
bounded background handoff, not synchronous enqueue in the detection/Slack path.
Anything still in that memory handoff at a crash is not yet durable.

## Opt-in camera adapter

`app/rook_capture.py` connects the engine to the local spool only. Default
`ROOK_EVENTS_ENABLED=0` performs no model fingerprinting, frame copies or spool
writes. When enabled it requires an explicit stable `ROOK_EVENT_DEVICE_ID`;
`ROOK_EVENT_OUTBOX` defaults to `~/rook-local/outbox/events.sqlite3`. No endpoint or
device credential belongs in the camera adapter. Enable the separate delivery
worker only after authenticated ingestion is ready. Pi rollout remains pending.

The handoff permits two outstanding frames total, including the one being written.
Each input must be uint8 BGR, three channels and at most 8 MiB. Submission validates
small metadata, assigns the event UUID and copies the raw buffer once. Copying is
CPU/memory work on the capture thread; it is not a hard real-time latency guarantee.
One daemon thread encodes native-size JPEG quality85, hashes its bytes, commits to
SQLite and writes diagnostic health. Images over the 2 MiB attachment cap are
rejected with a counted failure, never silently resized or substituted.

Capture time is recorded immediately after `capture_array` returns, before camera
orientation/inference; it is an acquisition timestamp, not a hardware shutter clock.
Boxes are normalized to the oriented raw frame. Label names are checked against the
loaded vocabulary. Detector observations retain corresponding boxes for the scene's
eligible labels and airborne confidence filter; selection is at class level, not
per-instance tracking or a claim that every fixture box was removed.

The adapter has an independent label-arrival gate: unchanged scenes are suppressed,
an observed absence rearms returning labels, and accepted detection observations
are at least10 seconds apart. A new label still present after throttling can then
be captured. Acceptance here means memory admission, not durable commit; a later
write failure is counted as lost capture and not silently replayed under a new ID.
Motion observations reuse the existing persistent-motion archive sampling gate.
They are not approved empty frames or training negatives. Slack's cooldowns,
quiet hours, scoring and lingerer rules are unchanged.

Startup, encoding, full-queue, disk and invalid-input failures are contained.
The adapter reports session counters in `capture_status.json`: memory admissions,
commits, handoff/full-outbox drops, write failures and invalid captures. It also
reports state and error class without exception details. Health writes are bounded
to at most once per5 seconds during activity, with a60-second idle heartbeat;
startup/shutdown state transitions can write immediately. Disk stalls can also
stall this health file, so the future dashboard must show stale status honestly.
This file is diagnostic, not an atomic per-event audit or a lifetime drop ledger.

Shutdown requests drain pending work but waits at most0.25 seconds for the writer;
a stuck writer cannot hold up camera teardown. The daemon's remaining memory work
can be lost when the process exits. Disk/HTTP waits are isolated, but encoding
still shares CPU and memory with inference. Measure latency/thermal impact on the
Pi before enabling; local failure tests do not replace that device check.

## Worker and receiver contract

`app/rook_event_worker.py` runs separately. It claims one record with an expiring
lease, performs HTTPS outside the database transaction, and only acknowledges the
current lease. A crash or stalled worker allows replay after lease expiry; an old
worker cannot delete or reschedule a newer worker's claim.

The request is JSON `{event_json, image_base64}` with an `Idempotency-Key` of
`device_id/event_id`. The bearer credential must be an opaque, revocable device
token restricted to ingestion, never a Supabase service credential. Token files
must be private regular files. HTTPS verification is enabled, environment proxies
are disabled, and redirects are rejected without forwarding credentials.

The receiver must authenticate the token's device binding, validate the payload
and attachment, and durably commit the event plus private image before replying.
Enforce a unique `(device_id, event_id)` key. Identical retries return the original
receipt; reuse with different content returns409. The successful200/201 receipt is:

```json
{
  "schema_version": 1,
  "device_id": "yard-pi",
  "event_id": "a0d7f250-9d54-49ef-bab4-326b3025f201",
  "event_sha256": "<SHA256 of canonical device event>",
  "received_at": "2026-10-12T17:00:00Z"
}
```

An arbitrary200,202,409 or malformed/mismatched receipt is not delivery proof.
Valid receipts delete only the matching leased row. Lost responses cause replay;
receiver deduplication prevents duplicate feed entries. This is at-least-once
delivery, not a claim that network requests occur exactly once.

Network errors,408/425/429/5xx and invalid receipts retry with persisted exponential
backoff starting at5 seconds, jitter, capped at1 hour. Other HTTP failures block
the item for explicit repair. Authentication failures and endpoint redirects/404
also stop the worker with exit2. Do not configure a supervisor to restart-loop on
exit2. There is no infinite per-record retry count cutoff or silent discard.
Only machine error codes are stored/logged, not response bodies or credentials.

## Operation and checks

Inspect locally without credentials or network access:

```bash
python3 app/rook_event_worker.py --db /local/path/events.sqlite3 status
```

Once a tested authenticated receiver and device token exist:

```bash
python3 app/rook_event_worker.py --db /local/path/events.sqlite3 send \
  --endpoint https://YOUR-INGEST-HOST/api/device/events \
  --token-file /private/config/device-token
```

Use `send --once` for one due attempt. After fixing a rejected credential/payload,
`retry-blocked` explicitly releases retained blocked records. Malformed retained
payloads require investigation; requeueing does not magically repair their bytes.
`status` reports occupancy, limits, oldest queued time and grouped error codes.

Run `python3 -m unittest discover -s tests -p test_event_outbox.py -v`. Tests cover
process-exit persistence, attachment binding, concurrent producers/workers, capacity,
lease recovery, stale acknowledgments, lost-response replay with a fake idempotent
receiver, corruption, rejected credentials, malformed receipts, HTTPS/redirect and
token-file restrictions. They use synthetic records and HTTP mocks, not yard images
or real service tokens. Hosted deduplication, RLS, revoked-token enforcement and a
real private image on the owner's phone remain milestone acceptance work.

`python3 -m unittest discover -s tests -p test_capture_adapter.py -v` covers the
producer: raw-buffer ownership, native JPEG decode, model/vocabulary consistency,
loading-time mutation, disabled behavior, deferred backend setup, stalled/full/
failed storage, bounded admission/shutdown, scene reappearance and a complete
capture→SQLite→mocked HTTPS receipt round trip. No real Pi camera or Slack messages
are used in these tests.
