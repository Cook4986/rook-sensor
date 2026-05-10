#!/usr/bin/env python3
"""
rook_eval.py — Rook Internal Performance Evaluation
────────────────────────────────────────────────────
Parses ~/rook.log (or a supplied path) and produces a Markdown report
covering detection quality, alert precision, thermal behavior, ghost-motion
rate, and lingerer performance.

Usage:
    python3 rook_eval.py                      # reads ~/rook.log
    python3 rook_eval.py /path/to/rook.log    # explicit path
    python3 rook_eval.py --json               # machine-readable JSON output

Output: rook_eval_report.md (and optionally rook_eval_report.json)
"""

import re
import sys
import json
import os
from collections import Counter, defaultdict
from datetime import datetime

# ── Regex patterns ────────────────────────────────────────────────────────────
RE_TS        = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
RE_IDENTIFIED = re.compile(r"Identified: (.+)$")
RE_FIXTURE   = re.compile(r"Fixture detected: '(\w[\w ]+)' at zone")
RE_LINGER    = re.compile(r"Lingering alert: (.+)$")
RE_GHOST     = re.compile(r"Ghost motion \(interest=(\d+\.\d+)\)\. Archiving")
RE_GHOST_LOW = re.compile(r"Ghost motion \(low visual interest: (\d+\.\d+)\)")
RE_TEMP      = re.compile(r"heartbeat.*?(\d+\.\d+)°C")
RE_STARTUP   = re.compile(r"Rook engine started")
RE_THERMAL_W = re.compile(r"THERMAL LIMIT|thermal shutdown", re.I)
RE_DIGEST    = re.compile(r"Daily digest sent")
RE_SLACK     = re.compile(r"Slack alert sent")
RE_EMAIL     = re.compile(r"Alert dispatched")
RE_FIXTURE_SUP = re.compile(r"Fixture suppressed: (\w[\w ]+) @")


def parse_log(path: str) -> dict:
    data = {
        "detections": [],        # (timestamp_str, emoji_str)
        "fixtures_promoted": [], # class names promoted to fixtures
        "fixtures_suppressed": Counter(),  # class → suppression count
        "linger_alerts": [],     # alert strings
        "ghost_archived": [],    # visual_interest scores (passed gate)
        "ghost_skipped": [],     # visual_interest scores (failed gate)
        "temps": [],             # (timestamp_str, float)
        "startups": [],          # timestamp strings
        "thermal_warnings": [],  # timestamp strings
        "slack_alerts": 0,
        "email_alerts": 0,
        "digests_sent": 0,
        "lines_parsed": 0,
        "log_start": None,
        "log_end": None,
    }

    with open(path, "r", errors="replace") as f:
        for line in f:
            data["lines_parsed"] += 1
            ts_match = RE_TS.match(line)
            ts = ts_match.group(1) if ts_match else None
            if ts:
                if data["log_start"] is None:
                    data["log_start"] = ts
                data["log_end"] = ts

            if m := RE_IDENTIFIED.search(line):
                data["detections"].append((ts, m.group(1).strip()))
            if m := RE_FIXTURE.search(line):
                data["fixtures_promoted"].append(m.group(1).strip())
            if m := RE_FIXTURE_SUP.search(line):
                data["fixtures_suppressed"][m.group(1).strip()] += 1
            if m := RE_LINGER.search(line):
                data["linger_alerts"].append(m.group(1).strip())
            if m := RE_GHOST.search(line):
                data["ghost_archived"].append(float(m.group(1)))
            if m := RE_GHOST_LOW.search(line):
                data["ghost_skipped"].append(float(m.group(1)))
            if m := RE_TEMP.search(line):
                data["temps"].append((ts, float(m.group(1))))
            if RE_STARTUP.search(line):
                data["startups"].append(ts)
            if RE_THERMAL_W.search(line):
                data["thermal_warnings"].append(ts)
            if RE_SLACK.search(line):
                data["slack_alerts"] += 1
            if RE_EMAIL.search(line):
                data["email_alerts"] += 1
            if RE_DIGEST.search(line):
                data["digests_sent"] += 1

    return data


def build_report(data: dict, log_path: str) -> str:
    lines = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines += [
        f"# 🦅 Rook Internal Evaluation Report",
        f"*Generated: {now} | Source: `{log_path}`*",
        f"*Log span: {data['log_start']} → {data['log_end']}*",
        "",
    ]

    # ── System Stability ──────────────────────────────────────────────────────
    lines += ["## 🖥️ System Stability", ""]
    lines.append(f"- **Engine restarts:** {len(data['startups'])}")
    if data["startups"]:
        lines.append(f"  - First: `{data['startups'][0]}`")
        lines.append(f"  - Last:  `{data['startups'][-1]}`")
    lines.append(f"- **Thermal warnings/shutdowns:** {len(data['thermal_warnings'])}")
    if data["thermal_warnings"]:
        for tw in data["thermal_warnings"]:
            lines.append(f"  - `{tw}`")
    if data["temps"]:
        tvals = [t for _, t in data["temps"]]
        lines.append(f"- **SoC temp range:** {min(tvals):.1f}°C – {max(tvals):.1f}°C "
                     f"(avg {sum(tvals)/len(tvals):.1f}°C over {len(tvals)} heartbeats)")
    lines.append(f"- **Digests sent:** {data['digests_sent']}")
    lines.append("")

    # ── Detection Volume ──────────────────────────────────────────────────────
    total_det = len(data["detections"])
    lines += ["## 📊 Detection Volume", ""]
    lines.append(f"- **Total YOLO detection events logged:** {total_det}")

    # Hour-of-day distribution
    hour_counts = Counter()
    for ts, _ in data["detections"]:
        if ts:
            try:
                hour_counts[int(ts[11:13])] += 1
            except Exception:
                pass
    if hour_counts:
        peak_hour = max(hour_counts, key=hour_counts.get)
        quiet_hour = min(hour_counts, key=hour_counts.get)
        lines.append(f"- **Peak hour:** {peak_hour:02d}:00 ({hour_counts[peak_hour]} events)")
        lines.append(f"- **Quietest hour:** {quiet_hour:02d}:00 ({hour_counts[quiet_hour]} events)")

    lines.append("")

    # ── Alert Dispatch ────────────────────────────────────────────────────────
    lines += ["## 🔔 Alert Dispatch", ""]
    lines.append(f"- **Slack alerts sent:** {data['slack_alerts']}")
    lines.append(f"- **Email alerts sent:** {data['email_alerts']}")
    if total_det > 0:
        slack_rate = data["slack_alerts"] / total_det * 100
        lines.append(f"- **Slack alert rate:** {slack_rate:.1f}% of detection events")
    lines.append("")

    # ── Fixture Filter ────────────────────────────────────────────────────────
    lines += ["## 📌 Scene Fixture Filter", ""]
    lines.append(f"- **Classes promoted to fixtures:** {len(data['fixtures_promoted'])}")
    for cls in set(data["fixtures_promoted"]):
        lines.append(f"  - `{cls}`")
    total_sup = sum(data["fixtures_suppressed"].values())
    lines.append(f"- **Fixture suppression events:** {total_sup}")
    if data["fixtures_suppressed"]:
        for cls, cnt in data["fixtures_suppressed"].most_common(5):
            lines.append(f"  - `{cls}`: {cnt}×")
    lines.append("")

    # ── Ghost Motion ──────────────────────────────────────────────────────────
    lines += ["## 👻 Ghost Motion (MOG2 False Triggers)", ""]
    archived = len(data["ghost_archived"])
    skipped  = len(data["ghost_skipped"])
    total_ghost = archived + skipped
    lines.append(f"- **Ghost frames archived (high visual interest):** {archived}")
    lines.append(f"- **Ghost frames skipped (low visual interest):**  {skipped}")
    if total_ghost > 0:
        lines.append(f"- **Gate efficiency:** {skipped/total_ghost*100:.1f}% of ghosts suppressed by visual-interest gate")
    if data["ghost_archived"]:
        avg_i = sum(data["ghost_archived"]) / len(data["ghost_archived"])
        lines.append(f"- **Avg interest score (archived ghosts):** {avg_i:.0f}")
    lines.append("")

    # ── Lingerer Tracker ──────────────────────────────────────────────────────
    lines += ["## ⏱️ Lingerer Tracker", ""]
    lines.append(f"- **Lingering alerts fired:** {len(data['linger_alerts'])}")
    for alert in data["linger_alerts"][:10]:
        lines.append(f"  - {alert}")
    if len(data["linger_alerts"]) > 10:
        lines.append(f"  - *(+ {len(data['linger_alerts']) - 10} more)*")
    lines.append("")

    # ── Recommendations ───────────────────────────────────────────────────────
    lines += ["## 💡 Automated Recommendations", ""]
    if len(data["thermal_warnings"]) > 0:
        lines.append("- ⚠️ **Thermal events detected** — review mounting position and ventilation.")
    if len(data["fixtures_promoted"]) > 3:
        lines.append("- ℹ️ Many fixtures promoted — scene may have persistent misclassifications; "
                     "consider adding to IGNORED_CLASSES.")
    if total_ghost > 0 and skipped / max(total_ghost, 1) < 0.5:
        lines.append("- ℹ️ Visual-interest gate is suppressing <50% of ghost frames — "
                     "consider raising the Laplacian variance threshold (currently 250).")
    if data["slack_alerts"] > 200 and total_det > 0 and data["slack_alerts"] / total_det > 0.5:
        lines.append("- ⚠️ High Slack alert rate — MIN_SLACK_SCORE may still be too low for this scene.")
    if not lines[-1].startswith("-"):
        lines.append("- ✅ No automated issues detected.")
    lines.append("")

    lines.append("---")
    lines.append("*Run `python3 rook_eval.py` after each week of operation to track trends.*")

    return "\n".join(lines)


def main():
    log_path = os.path.expanduser("~/rook.log")
    output_json = False

    for arg in sys.argv[1:]:
        if arg == "--json":
            output_json = True
        elif not arg.startswith("-"):
            log_path = arg

    if not os.path.exists(log_path):
        print(f"❌ Log not found: {log_path}")
        sys.exit(1)

    print(f"🔍 Parsing {log_path}...")
    data = parse_log(log_path)

    report_md = build_report(data, log_path)
    out_md = os.path.join(os.path.dirname(log_path), "rook_eval_report.md")
    with open(out_md, "w") as f:
        f.write(report_md)
    print(f"✅ Report written: {out_md}")

    if output_json:
        # Make Counter serializable
        data["fixtures_suppressed"] = dict(data["fixtures_suppressed"])
        out_json = out_md.replace(".md", ".json")
        with open(out_json, "w") as f:
            json.dump(data, f, indent=2, default=str)
        print(f"✅ JSON written:   {out_json}")

    # Print summary to stdout
    print("\n── Quick Summary ──────────────────────────────────────")
    print(f"  Detections:        {len(data['detections'])}")
    print(f"  Slack alerts:      {data['slack_alerts']}")
    print(f"  Email alerts:      {data['email_alerts']}")
    print(f"  Fixtures promoted: {len(data['fixtures_promoted'])}")
    print(f"  Ghost (archived):  {len(data['ghost_archived'])}")
    print(f"  Ghost (skipped):   {len(data['ghost_skipped'])}")
    print(f"  Thermal warnings:  {len(data['thermal_warnings'])}")
    print(f"  Engine restarts:   {len(data['startups'])}")


if __name__ == "__main__":
    main()
