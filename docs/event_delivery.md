# Device events and durable delivery

This is the local foundation for execution-plan milestone 3. The modules are not
connected to the camera loop or installed on the Pi yet. The receiver, owner app,
private storage and account setup are subsequent work. No network request occurs
on import, queue creation or queue inspection. Slack does not import these modules.

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

The upcoming producer must compute model identity from the actual loaded artifact
and hash the ordered name list with `canonical`. It must retain that artifact's
manifest with registration; a configured display name alone is insufficient proof.
Artifact hashing for NCNN directories is still part of producer integration.

`canonical` is UTF-8 JSON, sorted keys, compact separators, literal Unicode, no
NaN/Infinity. The event digest covers these exact bytes. The wire envelope carries
these bytes as an `event_json` string. The receiver hashes its UTF-8 bytes directly
before parsing; it must not reserialize the parsed object to calculate the digest.
This preserves Python number formatting through a JavaScript receiver.

Each event is at most 64 KiB; an attachment is at most 2 MiB. The spool verifies
attachment length and SHA256 against its reference. It does not decode JPEGs or
prove that pixels are unannotated. The capture adapter must encode a raw frame,
provide actual dimensions, and never use `results.plot()` or an alert preview.
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

SQLite I/O is synchronous and can block or fail. The capture adapter must use a
small bounded background handoff, not call enqueue from the detection/Slack path.
Anything still in that memory handoff at a crash is not yet durable; report that
boundary honestly. This adapter and its failure-isolation tests remain to be built.

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
