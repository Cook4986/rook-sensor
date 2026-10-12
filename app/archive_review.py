#!/usr/bin/env python3
"""Private loopback review of catalog batches; records human decisions locally.

This is the local review component, not the hosted v2 app. Decisions never edit the
legacy cache or a training dataset. Applying them to an approved snapshot is separate.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import subprocess
import sys
import threading
from urllib.parse import parse_qs, urlparse

from archive_catalog import require_local
from train_custom_model import CUSTOM_CLASSES


def read_decisions(path):
    latest = {}
    if path.exists():
        for line in path.read_text().splitlines():
            record = json.loads(line)
            latest[record["item_id"]] = record
    return latest


def load_items(catalog):
    items = []
    for name in ("review_batch.json", "discovery_batch.json"):
        for item in json.loads((catalog / name).read_text()):
            item = dict(item)
            item["item_id"] = item.get("cache_hash") or hashlib.sha256(
                (item["kind"] + item["source_filename"]).encode()).hexdigest()
            items.append(item)
    return {i["item_id"]: i for i in items}


def append_decision(path, item, decision, corrected_label=None):
    is_candidate = item["kind"] == "cached_candidate"
    allowed = {"approve", "correct", "reject", "uncertain"} if is_candidate else {"interesting", "background", "uncertain"}
    if decision not in allowed:
        raise ValueError("Invalid decision for this item")
    if decision == "correct" and (corrected_label not in CUSTOM_CLASSES or corrected_label == "flood"):
        raise ValueError("Select a supported, active class")
    record = {
        "schema_version": 1, "item_id": item["item_id"],
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "decision": decision, "corrected_label": corrected_label if decision == "correct" else None,
        "scope": "candidate_identity" if is_candidate else "discovery_triage",
        "proposal": item, "applied_to_training": False,
    }
    # Never silently overwrite decisions. Identical browser retries are idempotent;
    # subsequent changed decisions append and supersede by position.
    with path.open("a+") as output:
        fcntl.flock(output, fcntl.LOCK_EX)
        output.seek(0)
        previous = None
        for line in output:
            old = json.loads(line)
            if old["item_id"] == item["item_id"]:
                previous = old
        if previous and all(previous.get(k) == record.get(k) for k in ("decision", "corrected_label", "proposal")):
            return previous
        output.write(json.dumps(record) + "\n")
        output.flush()
        import os
        os.fsync(output.fileno())
    return record


def make_server(catalog, archive, staging, port=0):
    catalog = require_local(catalog)
    roots = [Path(p).expanduser().resolve() for p in (archive, staging)]
    items = load_items(catalog)
    ledger = catalog / "review_decisions.jsonl"
    # Fail at startup on damaged history rather than hiding review work.
    read_decisions(ledger)
    token = secrets.token_urlsafe(32)
    lock = threading.Lock()
    page = Path(__file__).with_name("archive_review.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Do not write access tokens or private filenames into request logs.

        def reply(self, status, body=b"", content_type="application/json", extra=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self'; frame-ancestors 'none'; form-action 'none'; base-uri 'none'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                return False
            try:
                jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
                supplied = jar["rook_review"].value if "rook_review" in jar else ""
                return secrets.compare_digest(supplied, token)
            except cookies.CookieError:
                return False

        def do_GET(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            supplied = query.get("token", [""])[0]
            if parsed.path == "/" and supplied and self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}" and secrets.compare_digest(supplied, token):
                self.reply(303, extra={"Location": "/", "Set-Cookie": f"rook_review={token}; HttpOnly; SameSite=Strict; Path=/"})
                return
            if not self.authorized():
                self.reply(403, b'{"error":"Open the private startup link to review."}')
                return
            if parsed.path == "/":
                self.reply(200, page, "text/html; charset=utf-8")
            elif parsed.path == "/api/items":
                with lock:
                    decisions = read_decisions(ledger)
                self.reply(200, json.dumps({"items": list(items.values()), "decisions": decisions,
                                           "classes": [c for c in CUSTOM_CLASSES if c != "flood"]}).encode())
            elif parsed.path == "/image":
                item = items.get(query.get("item", [""])[0])
                if not item:
                    self.reply(404); return
                paths = [item.get("gallery_path")] if query.get("view") == ["gallery"] else item.get("source_paths", [])
                for value in paths:
                    if not value:
                        continue
                    path = Path(value).resolve()
                    if path.suffix.lower() not in (".jpg", ".jpeg") or not any(path.is_relative_to(root) for root in roots):
                        continue
                    try:
                        # Bound a Dropbox stall and reject unexpectedly large media.
                        proc = subprocess.run([sys.executable, "-c",
                            "import sys; f=open(sys.argv[1],'rb'); b=f.read(10485761); "
                            "sys.exit(2) if len(b)>10485760 else sys.stdout.buffer.write(b)", str(path)],
                            capture_output=True, timeout=8, check=True)
                        self.reply(200, proc.stdout, "image/jpeg"); return
                    except (OSError, subprocess.SubprocessError):
                        continue
                self.reply(404, b'{"error":"Image unavailable; defer this item."}')
            else:
                self.reply(404)

        def do_POST(self):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if not self.authorized() or self.headers.get("Origin") != origin:
                self.reply(403); return
            if self.path != "/api/decisions":
                self.reply(404); return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16384 or self.headers.get("Content-Type") != "application/json":
                    raise ValueError("Invalid request")
                data = json.loads(self.rfile.read(length))
                item = items[data["item_id"]]
                with lock:
                    record = append_decision(ledger, item, data["decision"], data.get("corrected_label"))
                self.reply(200, json.dumps(record).encode())
            except (ValueError, KeyError, TypeError) as exc:
                self.reply(400, json.dumps({"error": str(exc)}).encode())
            except OSError:
                self.reply(500, b'{"error":"Decision was not saved; retry after checking local storage."}')

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return server, f"http://127.0.0.1:{server.server_port}/?token={token}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=Path.home() / "rook-local/catalog")
    parser.add_argument("--archive", type=Path, default=Path.home() / "Library/CloudStorage/Dropbox/Rook/archive")
    parser.add_argument("--staging", type=Path, default=Path.home() / "rook-staging")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    server, url = make_server(args.catalog, args.archive, args.staging, args.port)
    print(f"Private local review: {url}", flush=True)
    print("Human decisions stay local; no training or legacy-cache changes.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
