#!/usr/bin/env python3
"""Read-only archive inventory and review queue. No image inference or paid calls.

The SQLite database and JSON exports are derived local artifacts. Source images,
cache and human verdicts are never changed. Same filenames indicate possible mirror
copies, NOT verified content duplicates. Time buckets are sampling aids, NOT events.
"""
import argparse
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile

COLLECTIONS = ("unclassified", "classified", "reclassified", "processed", "beast_cam")
DEFAULT_ARCHIVE = Path.home() / "Library/CloudStorage/Dropbox/Rook/archive"


def require_local(path):
    path = Path(path).expanduser().resolve()
    if "CloudStorage" in path.parts or "Dropbox" in path.parts:
        raise ValueError(f"Derived working files must be on local disk: {path}")
    return path


def capture_time(path):
    match = re.search(r"(20\d{6})_(\d{6})", path.name)
    if match:
        value = "".join(match.groups())
    elif re.fullmatch(r"20\d{2}-\d{2}-\d{2}", path.parent.name):
        match = re.search(r"_(\d{6})_", path.name)
        value = path.parent.name.replace("-", "") + match[1] if match else ""
    else:
        value = ""
    try:
        # Device-local time, deliberately no invented UTC offset.
        return datetime.strptime(value, "%Y%m%d%H%M%S").isoformat()
    except ValueError:
        return None


def sample_group(timestamp, filename):
    if not timestamp:
        return "undated:" + filename
    dt = datetime.fromisoformat(timestamp)
    return dt.replace(minute=dt.minute // 5 * 5, second=0).isoformat()


def snapshot_text(source, destination, errors, timeout=8):
    """Bound File Provider reads in a subprocess; preserve failure as evidence."""
    try:
        subprocess.run(
            [sys.executable, "-c", "import shutil,sys; shutil.copyfile(sys.argv[1],sys.argv[2])",
             str(source), str(destination)], check=True, timeout=timeout,
            capture_output=True,
        )
        return destination.read_text()
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        errors.append({"source": str(source), "error": type(exc).__name__})
        return None


def inventory(root, origin, errors):
    for collection in COLLECTIONS:
        folder = root / collection
        try:
            # Never recurse into reclassified/annotated or training/review renders.
            if collection == "beast_cam":
                directories = sorted(p for p in folder.iterdir()
                                     if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", p.name)
                                     and p.is_dir() and not p.is_symlink())
            else:
                directories = [folder]
            for directory in directories:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if not entry.name.lower().endswith((".jpg", ".jpeg")) or entry.is_symlink():
                            continue
                        path = Path(entry.path)
                        try:
                            stat = entry.stat(follow_symlinks=False)
                        except OSError as exc:
                            errors.append({"source": str(path), "error": str(exc)})
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        timestamp = capture_time(path)
                        yield (str(path), origin, collection, path.name, stat.st_size,
                               timestamp, sample_group(timestamp, path.name))
        except OSError as exc:
            errors.append({"source": str(folder), "error": str(exc)})


def read_metadata(archive, work, errors):
    dataset = archive / "autolabel"
    text = snapshot_text(dataset / "autolabel_cache.jsonl", work / "cache.jsonl", errors)
    cache = {}
    if text is not None:
        for line_no, line in enumerate(text.splitlines(), 1):
            try:
                rec = json.loads(line)
                if not isinstance(rec, dict) or not isinstance(rec.get("hash"), str):
                    raise ValueError("missing hash")
                cache[rec["hash"]] = rec
            except (ValueError, TypeError):
                errors.append({"source": "autolabel_cache.jsonl", "line": line_no,
                               "error": "invalid record"})
    manifests = {}
    for name in ("review_manifest.json", "candidates_manifest.json"):
        text = snapshot_text(dataset / "review" / name, work / name, errors)
        try:
            manifests[name] = json.loads(text) if text is not None else {}
            if not isinstance(manifests[name], dict):
                raise ValueError("expected object")
        except (ValueError, TypeError) as exc:
            errors.append({"source": name, "error": str(exc)})
            manifests[name] = {}
    return cache, manifests


def candidate_queue(archive, cache, manifests, locations, errors):
    paths_by_name = defaultdict(list)
    for row in locations:
        paths_by_name[row[3]].append(row)
    folder = archive / "autolabel/review/candidates"
    try:
        if not folder.is_dir():
            raise FileNotFoundError(str(folder))
        gallery = sorted(folder.rglob("*.jpg"))
    except OSError as exc:
        errors.append({"source": str(folder), "error": str(exc)})
        gallery = []
    pending, ignored, unresolved, seen = [], 0, [], set()
    for path in gallery:
        item = manifests["candidates_manifest.json"].get(path.name)
        if not isinstance(item, dict) or not item.get("hash"):
            unresolved.append(str(path))
            continue
        key = item["hash"]
        verdict = cache.get(key)
        if verdict is None:
            # A stale manifest is not authoritative after a failed cache read.
            unresolved.append(str(path))
            continue
        if verdict.get("reviewed") or verdict.get("label") in (None, "none", "flood"):
            ignored += 1
            continue
        if key in seen:
            continue
        seen.add(key)
        source = verdict.get("source", item.get("source", ""))
        matches = paths_by_name.get(source, [])
        # Prefer local staging for display. Paths remain possible copies until hashed.
        matches.sort(key=lambda x: (x[1] != "staging", x[0]))
        stamp = matches[0][5] if matches else None
        pending.append({"kind": "cached_candidate", "cache_hash": key,
                        "label": verdict.get("label"), "confidence": verdict.get("confidence"),
                        "source_filename": source, "source_paths": [r[0] for r in matches],
                        "gallery_path": str(path), "capture_time": stamp,
                        "sample_group": sample_group(stamp, source),
                        "review_state": "pending", "human_decision": None})
    return pending, {"gallery_files": len(gallery), "excluded_reviewed_or_retired": ignored,
                     "unresolved_gallery": unresolved}


def balanced_batch(items, limit):
    """Round-robin labels; at most one label/source capture group per batch."""
    dates = defaultdict(lambda: defaultdict(deque))
    for item in sorted(items, key=lambda x: (x.get("capture_time") or "", x["source_filename"]), reverse=True):
        dates[item.get("label", "unknown")][(item.get("capture_time") or "undated")[:10]].append(item)
    groups = defaultdict(deque)
    for label, days in dates.items():
        while days:
            for day in sorted(list(days), reverse=True):
                groups[label].append(days[day].popleft())
                if not days[day]:
                    del days[day]
    result, seen = [], set()
    while groups and len(result) < limit:
        for label in list(sorted(groups)):
            if len(result) >= limit:
                break
            queue = groups[label]
            while queue:
                item = queue.popleft()
                key = (label, item["sample_group"])
                if key not in seen:
                    seen.add(key)
                    result.append(item)
                    break
            if not queue:
                del groups[label]
    return result


def build_catalog(archive, staging, output, batch_size=40):
    archive = Path(archive).expanduser().resolve()
    staging = Path(staging).expanduser().resolve() if staging else None
    output = require_local(output)
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    locations = list(inventory(archive, "archive", errors))
    if staging:
        # Staging intentionally contains only the three Pi source collections.
        staged_errors = []
        locations.extend(inventory(staging, "staging", staged_errors))
        errors.extend(e for e in staged_errors
                      if Path(e["source"]).name not in ("reclassified", "processed"))
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        cache, manifests = read_metadata(archive, Path(tmp), errors)
        pending, review_summary = candidate_queue(archive, cache, manifests, locations, errors)
        decisions = {}
        decision_path = output / "review_decisions.jsonl"
        if decision_path.exists():
            try:
                for line in decision_path.read_text().splitlines():
                    record = json.loads(line)
                    decisions[record["item_id"]] = record
            except (OSError, ValueError, KeyError) as exc:
                errors.append({"source": str(decision_path), "error": str(exc)})
        def needs_review(item):
            key = item.get("cache_hash") or hashlib.sha256(
                (item["kind"] + item["source_filename"]).encode()).hexdigest()
            proposal = decisions.get(key, {}).get("proposal", {})
            return any(proposal.get(k) != item.get(k) for k in ("kind", "label", "source_filename"))
        batch = balanced_batch([i for i in pending if needs_review(i)], batch_size)
        cached_names = {r.get("source") for r in cache.values()}
        unknowns = {}
        for row in sorted(locations, key=lambda r: r[1] == "staging"):
            path, origin, collection, name, size, stamp, group = row
            if collection in ("unclassified", "processed") and name not in cached_names and size:
                unknowns[name] = {
                    "kind": "uncached_by_filename", "label": "unknown",
                    "source_filename": name, "source_paths": [path], "collection": collection,
                    "capture_time": stamp, "sample_group": group,
                    "review_state": "not_screened", "human_decision": None,
                }
        discovery = balanced_batch([i for i in unknowns.values() if needs_review(i)], batch_size)
        database = Path(tmp) / "catalog.sqlite3"
        with sqlite3.connect(database) as db:
            db.executescript('''
              CREATE TABLE locations(path TEXT PRIMARY KEY, origin TEXT, collection TEXT,
                filename TEXT, bytes INTEGER, capture_time TEXT, sample_group TEXT);
              CREATE INDEX locations_filename ON locations(filename);
              CREATE TABLE verdicts(hash TEXT PRIMARY KEY, source TEXT, label TEXT,
                confidence REAL, reviewed TEXT, record_json TEXT);
              CREATE TABLE review_queue(cache_hash TEXT PRIMARY KEY, record_json TEXT);
              CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT);
            ''')
            db.executemany("INSERT INTO locations VALUES (?,?,?,?,?,?,?)", locations)
            db.executemany("INSERT INTO verdicts VALUES (?,?,?,?,?,?)", [
                (key, r.get("source"), r.get("label"), r.get("confidence"),
                 r.get("reviewed"), json.dumps(r)) for key, r in cache.items()])
            db.executemany("INSERT INTO review_queue VALUES (?,?)", [
                (r["cache_hash"], json.dumps(r)) for r in pending])
            summary = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "complete": not errors, "errors": errors,
                "location_counts": dict(Counter(f"{r[1]}/{r[2]}" for r in locations)),
                "filename_groups_not_content_deduplicated": len({r[3] for r in locations}),
                "zero_byte_locations": sum(r[4] == 0 for r in locations),
                "effective_cache_verdicts": len(cache),
                "cache_review_states": dict(Counter(r.get("reviewed", "unmarked") for r in cache.values())),
                "candidate_labels": dict(Counter(r["label"] for r in pending)),
                "pending_candidates": len(pending), "review_batch_size": len(batch),
                "local_review_decisions_not_applied": len(decisions),
                "uncached_filename_groups": len(unknowns),
                "discovery_batch_size": len(discovery), **review_summary,
                "limits": ["No image bytes read or content hashes verified",
                           "Filename matches are provisional, not duplicate proof",
                           "Five-minute groups aid sampling; they are not confirmed events",
                           "Queue is not human approval or a training dataset"],
            }
            db.execute("INSERT INTO metadata VALUES (?,?)", ("summary", json.dumps(summary)))
        database.replace(output / "catalog.sqlite3")
        for name, value in (("summary.json", summary), ("review_batch.json", batch),
                            ("discovery_batch.json", discovery)):
            target = Path(tmp) / name
            target.write_text(json.dumps(value, indent=2) + "\n")
            target.replace(output / name)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--staging", type=Path, default=Path.home() / "rook-staging")
    parser.add_argument("--output", type=Path, default=Path.home() / "rook-local/catalog")
    parser.add_argument("--batch-size", type=int, default=40)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    summary = build_catalog(args.archive, args.staging, args.output, args.batch_size)
    print(json.dumps(summary, indent=2))
    return 0 if summary["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
