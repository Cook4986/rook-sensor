# Private dashboard pilot

The `web/` Next.js app accepts version 1 observations from the existing outbox
worker and serves a private owner feed. `supabase/migrations/` owns its database
schema. Neither component is hosted yet. The Pi's capture adapter remains disabled.
This implements part of execution-plan milestone 3; it does not complete phone,
push, archive-review or live device-health acceptance.

## Local preview

Use Node 24 and the committed lockfile. Keep dependencies and build output on
local disk, outside Dropbox. From the repository root:

```bash
mkdir -p /private/tmp/rook-web-runtime
rsync -a --exclude=node_modules --exclude=.next web/ /private/tmp/rook-web-runtime/
npm ci --prefix /private/tmp/rook-web-runtime
npm run dev --prefix /private/tmp/rook-web-runtime -- --port 56610
```

Open `http://127.0.0.1:56610/preview`. Its three illustrated observations are
synthetic; that route never reads owner data and is not an authentication bypass.
The private home and sign-in pages explain that configuration is missing until
connected. The feed shows the latest 50 observations, original capture time,
server receipt time, detector confidence and model identity. A vehicle detection
does not imply a delivery. Last receipt is not proof that the sensor is online.

## Access and ingestion

- Email/password sign-in uses Supabase Auth through server actions. There is no
  registration UI. Disable public signups in the actual Supabase project before
  deployment; the application cannot enforce that provider setting by itself.
- The operator pre-provisions one Auth user and registers its UUID in `rook_owners`.
  Every feed/image request verifies the user through Auth and checks membership.
  Postgres RLS separately limits reads to the owner. No anonymous table reads or
  authenticated client writes are granted. Cookies are HTTP-only, SameSite Lax and
  secure in production; protected responses are private/no-store.
- Device credentials are random 256-bit opaque tokens. Only their SHA256 hashes
  are stored, bound to one device. The Pi gets no Supabase elevated credential.
  Only the web server's service role may invoke the ingestion/authentication RPCs.
- Ingestion authenticates before decoding the bounded body. It validates the
  closed event schema, canonical base64, SHA256, JPEG decoding/dimensions and model
  vocabulary. The database rechecks token revocation inside the final transaction.
  Model registrations pin artifact files and the ordered class list; operator
  registration is not training certification.
- `(device_id,event_id)` deduplicates replay, including concurrent submissions.
  Exact `event_json` bytes bind the receipt; Python float formatting is preserved.
  Changed content under an existing ID returns409. The worker's receipt validation
  and persisted retries are described in [event delivery](event_delivery.md).

## Images, quotas and retention

For this bounded pilot, JPEG bytes live in `rook_images.content` (`bytea`). The
image, event and receipt commit together. This avoids acknowledging an event whose
image was never durably stored, and avoids orphaned storage objects. D17 records
this choice. Images are served by an owner-authenticated endpoint with no public
bucket or reusable signed link; possession of a URL alone grants nothing.

Limits per device: 1,000 events or 256 MiB of logical event/image payload, whichever
comes first; each JPEG is at most 2 MiB and decodes to at most 8,388,608 pixels.
Database indexes, JSON copies, backups and provider overhead exceed logical quota
accounting: these bounds are not a hosting cost ceiling. Full storage returns429;
identical replays still return their original receipt. The Pi retries and its own
bounded outbox can eventually fill. Nothing silently deletes archived evidence.

There is no cloud retention/delete UI or quota monitor yet. Choose retention,
cost controls and operational visibility before sustained capture. A later private
object store needs a pending/finalized upload protocol and cleanup that preserves
the same durable-acknowledgment contract. Do not simply write an event then return
success before its upload has completed.

## Hosting and provisioning runbook

Prerequisites: chosen existing Supabase and Vercel projects, sole owner's email,
region and approved spending limit if new resources are required. Project access
may use existing CLI/browser sessions or secure local configuration. Never paste
keys into chat or commit them. In Vercel, use repository root directory `web` and
the Next.js preset. Apply the migration to the intended Supabase project once,
after inspecting it; use a separate test project for hosted acceptance.

Configure these only on the server (no `NEXT_PUBLIC_` variables):

- `SUPABASE_URL`: the selected project's API URL.
- `SUPABASE_PUBLISHABLE_KEY`: its public API key, used with the owner's session.
- `SUPABASE_SECRET_KEY`: elevated server key, used only by device RPC calls.
- `DATABASE_URL`: operator-only Postgres connection, never needed by the running
  web app; require verified TLS for remote database access.

Disable public Auth registration, create the owner Auth account through the
provider's supported flow and record its UUID. Account/credential entry belongs
in the provider or a private local configuration, not source code or logs.
Use a model manifest generated by `rook_capture.py` beside its spool; verify that
it corresponds to the artifact actually loaded on the Pi. From the local web copy:

```bash
node_modules/.bin/tsx scripts/provision.ts \
  --owner OWNER_AUTH_UUID --device yard-pi \
  --model /local/path/models/ARTIFACT-CLASSES.json \
  --token-file /private/config/rook-device-token
```

`DATABASE_URL` must already be supplied securely to that process. The token file
must be new, outside the checkout and synced folders. It is created mode0600 and
never printed. The CLI verifies manifest hashes, refuses changed registrations,
and binds device/owner/model/token in a transaction. If commit outcome is uncertain,
it retains the private file and reports uncertainty; inspect the registration
before using or retrying it. Provisioning again with a new file adds a token; it
does not revoke an older one. Keep a tested recovery path before rotation.

To revoke all device tokens as the database operator:

```sql
update rook_private.device_tokens set revoked_at=now()
where device_id='yard-pi' and revoked_at is null;
```

For a single token, additionally filter its `token_hash`. Revocation serializes
with in-flight commits: an already-committing observation may finish before the
revocation transaction, but subsequent submissions must fail. Transfer the chosen
token securely to the Pi's existing worker configuration; do not enable capture
until the hosted acceptance and Pi performance checks below pass.

## Verification and remaining acceptance

`npm run build` performs the production build and TypeScript checks. Unit/handler
tests require `ROOK_TEST_PYTHON` and `ROOK_APP_PATH` to exercise the actual Python
canonical event protocol. The database suite additionally needs a **disposable**
UTF8 database named `rook_test` on a Unix socket whose directory matches
`/private/tmp/rook-pg-*`; set `ROOK_PG_TEST_DIR` and `ROOK_MIGRATION_PATH`.
Never point these tests at a hosted or production database. Each run requires a
fresh database; without the socket variable the database test is explicitly skipped.

```bash
ROOK_TEST_PYTHON=/path/to/python3 ROOK_APP_PATH=/absolute/rook-sensor/app \
ROOK_PG_TEST_DIR=/private/tmp/rook-pg-TEST \
ROOK_MIGRATION_PATH=/absolute/rook-sensor/supabase/migrations/202610120001_private_feed.sql \
npm test --prefix /private/tmp/rook-web-runtime
```

Eight tests passed locally with no skips on Oct12, including real Postgres16 RLS,
denied anonymous/non-owner reads and RPC writes, revoked tokens, concurrent/restarted
replay, model/vocabulary rejection, late image-write rollback, quotas, private token
provisioning and actual Python-to-JavaScript event hashing. The local database uses
a test Auth schema and simulated JWT role context. This is not a hosted Supabase
Auth/PostgREST integration test. Desktop and 390px mobile preview were checked.

Before enabling a real feed: verify actual owner login/logout and cookie refresh,
signed-out/non-owner reads through both app and direct PostgREST, image bytea
transport, RPC grants, token revocation, HTTPS replay after a lost response, hosting
request limits and no-store behavior. Then verify a real image on the owner's phone
and Pi latency/temperature with capture enabled. Add device-health reporting,
archive review and PWA push afterward. Preserve Slack throughout the pilot.

Implementation references: [Next.js authentication](https://nextjs.org/docs/app/guides/authentication),
[Supabase server-side clients](https://supabase.com/docs/guides/auth/server-side/creating-a-client?framework=nextjs),
and [Supabase RLS](https://supabase.com/docs/guides/database/postgres/row-level-security).
