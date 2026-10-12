# Rook Dashboard v2 — Experience and architecture

The approved [execution plan](execution_plan.md) owns delivery order and acceptance
checks. Workspace D8/D11 were amended October 12, 2026: the private dashboard can
ship using current generic detections while custom-model work continues. WORKSTATE
owns implementation status. There is no existing hosted event API or config-poll
loop to reuse; those were design proposals in older versions of this document.

## Experience

A mobile-first, owner-only web app for yard activity, wildlife, weather context and
local-object discovery. The primary screen is a visual, emoji-forward event feed.
Use the current detector honestly: generic labels and uncertainty remain visible
until a validated subclass model exists. A delivery van does not prove a package
was delivered; a species suggestion is not an identification.

The initial screens are:

- **Home/feed:** dated event cards with private images, class/confidence and model
  provenance; weather is explicitly external context.
- **Event detail:** original image, detections and corrected labels, without
  confusing an annotated preview with a raw training source.
- **Review:** manageable archive batches, approve/correct/reject/uncertain actions
  with preserved human decisions. Separate identity triage from box approval.
- **Device:** last received event, connectivity, model identity and device health.
- **Wildlife:** saved sightings grouped by verified identity when available;
  unknown subjects remain useful review candidates.

Dark mode, black/white base and a single amber accent follow the existing concepts.
Workspace mockups (outside git) are `Media/dashboard_v2_mockup_home_feed.png`,
`Media/dashboard_v2_mockup_wildlife.png` and `Media/dashboard_v2_mockup_setup.png`.
The June `rook-app-experience.canvas.tsx` is historical exploration, not an
implementation dependency. Consumer pairing, live video and multi-user access are
outside the first slice; owner provisioning can be performed during setup.

## Data and access boundaries

The intended stack is Next.js, Supabase and Vercel. Detection writes versioned
local events to a bounded durable outbox; a separate worker sends them over outbound
HTTPS. Slack continues independently. The Pi exposes no inbound web service and
holds only a revocable device-scoped ingestion credential, never an elevated
Supabase credential. Retries use stable device/event IDs for deduplication.

Pre-provision the owner and disable public registration. Enforce owner authorization
on server routes, database rows and private image storage. Serve images through
short-lived authorized access. Verify signed-out/non-owner denial, revoked-device
denial and offline/replay behavior before using real remote images.

The event contract must distinguish captured and received time, generic detector
boxes, optional custom classifications, image reference and model version. Preserve
uncertainty, source evidence and human corrections. A stock model without a custom
model card still has a usable, explicit model identity.

## Notifications and rollout

SMS is no longer the product path (D9/D10). Keep working Slack alerts during the
pilot. Add an installable PWA and opt-in push after the private feed works, with
durable notification jobs, delivery failure handling and authenticated deep links.
Verify current platform requirements and test on the owner's actual phone; do not
promise Focus/Do Not Disturb bypass or emergency-grade delivery.

Before hosting, confirm the Vercel/Supabase projects, authorized owner identity,
region, deployment budget and required account access with the user. Check existing
connections before asking for credentials; use secure local/service configuration,
not secrets pasted into chat. Prepare code and tests locally while access is pending.
Retire stale SMS-facing documentation as part of rollout without inventing carrier
status or claiming an account/campaign has been closed.
