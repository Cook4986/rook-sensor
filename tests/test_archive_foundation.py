import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))
from archive_catalog import balanced_batch, build_catalog, capture_time, require_local


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / "archive"
        for kind in ("classified", "unclassified", "reclassified", "processed", "beast_cam"):
            (self.archive / kind).mkdir(parents=True)
        self.review = self.archive / "autolabel/review"
        (self.review / "candidates/work_truck").mkdir(parents=True)
        (self.review / "review_manifest.json").write_text("{}")
        self.cache = self.archive / "autolabel/autolabel_cache.jsonl"
        self.cache.write_text("")
        (self.review / "candidates_manifest.json").write_text("{}")
        self.out = self.root / "output"

    def run_catalog(self):
        return build_catalog(self.archive, None, self.out, 5)

    def test_last_review_wins_and_missing_provenance_is_not_approved(self):
        records = []
        manifest = {}
        for key in ("pending", "rejected", "approved", "orphan"):
            filename = key + ".jpg"
            (self.review / "candidates/work_truck" / filename).write_bytes(b"review render")
            manifest[filename] = {"hash": key, "source": "unclassified_20261012_071200.jpg"}
            if key != "orphan":
                records.append({"hash": key, "label": "work_truck", "confidence": .7,
                                "source": "unclassified_20261012_071200.jpg"})
        records.extend([{"hash": "rejected", "label": "none", "reviewed": "rejected"},
                        {"hash": "approved", "label": "work_truck", "reviewed": "approved"}])
        self.cache.write_text("".join(json.dumps(r) + "\n" for r in records))
        (self.review / "candidates_manifest.json").write_text(json.dumps(manifest))
        before = self.cache.read_bytes()
        summary = self.run_catalog()
        batch = json.loads((self.out / "review_batch.json").read_text())
        self.assertEqual([r["cache_hash"] for r in batch], ["pending"])
        self.assertIsNone(batch[0]["human_decision"])
        self.assertEqual(summary["excluded_reviewed_or_retired"], 2)
        self.assertEqual(len(summary["unresolved_gallery"]), 1)
        self.assertEqual(self.cache.read_bytes(), before)

    def test_rerun_retains_same_name_locations_without_inflating(self):
        name = "unclassified_20261012_071200.jpg"
        for folder in ("unclassified", "processed"):
            (self.archive / folder / name).write_bytes(b"not a decoded image")
        self.run_catalog()
        second = self.run_catalog()
        with sqlite3.connect(self.out / "catalog.sqlite3") as db:
            self.assertEqual(db.execute("select count(*) from locations").fetchone()[0], 2)
        self.assertEqual(second["filename_groups_not_content_deduplicated"], 1)

    def test_missing_sources_and_bad_cache_are_reported(self):
        (self.archive / "processed").rmdir()
        self.cache.write_text('invalid json\n')
        summary = self.run_catalog()
        self.assertFalse(summary["complete"])
        self.assertEqual(len(summary["errors"]), 2)

    def test_discovery_is_sampled_without_claiming_negative(self):
        for name in ("unclassified_20261012_071200.jpg", "unclassified_20261012_071201.jpg"):
            (self.archive / "unclassified" / name).write_bytes(b"raw")
        summary = self.run_catalog()
        self.assertEqual(summary["uncached_filename_groups"], 2)
        self.assertEqual(summary["discovery_batch_size"], 1)
        item = json.loads((self.out / "discovery_batch.json").read_text())[0]
        self.assertEqual(item["label"], "unknown")
        self.assertIsNone(item["human_decision"])

    def test_local_path_and_capture_time(self):
        with self.assertRaises(ValueError):
            require_local(self.root / "CloudStorage/Dropbox/catalog")
        self.assertEqual(capture_time(Path("2026-10-12/bird_071200_001_0.jpg")),
                         "2026-10-12T07:12:00")
        self.assertIsNone(capture_time(Path("unclassified_20269999_071200.jpg")))

    def test_batch_spreads_dates_and_classes(self):
        items = [{"label": label, "capture_time": day + "T07:00:00",
                  "source_filename": label + day + str(n), "sample_group": day + str(n)}
                 for label in ("work_truck", "usps_truck")
                 for day in ("2026-10-11", "2026-10-12") for n in range(4)]
        batch = balanced_batch(items, 4)
        self.assertEqual({(i["label"], i["capture_time"][:10]) for i in batch},
                         {(label, day) for label in ("work_truck", "usps_truck")
                          for day in ("2026-10-11", "2026-10-12")})

    def test_local_triage_survives_catalog_refresh(self):
        from archive_review import append_decision, load_items
        (self.archive / "unclassified/unclassified_20261012_071200.jpg").write_bytes(b"raw")
        self.run_catalog()
        item = next(iter(load_items(self.out).values()))
        ledger = self.out / "review_decisions.jsonl"
        append_decision(ledger, item, "interesting")
        before = ledger.read_bytes()
        summary = self.run_catalog()
        self.assertEqual(summary["discovery_batch_size"], 0)
        self.assertEqual(summary["local_review_decisions_not_applied"], 1)
        self.assertEqual(ledger.read_bytes(), before)
        self.assertEqual(self.cache.read_bytes(), b"")
        followups = json.loads((self.out / "discovery_followups.json").read_text())
        self.assertEqual(followups[0]["item_id"], item["item_id"])
        self.assertFalse(followups[0]["applied_to_training"])


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.staging = self.root / "staging"
        self.archive = self.root / "archive"
        (self.staging / "classified").mkdir(parents=True)
        (self.staging / "classified/classified_20261012_071200.jpg").write_bytes(b"raw frame")
        self.stub("ssh", '''#!/bin/bash
case "$ROOK_TEST_FAILURE" in
  connection) exit 255;;
  listing) if [[ "${!#}" == find* ]]; then exit 1; fi;;
  transfer)
    if [[ "${!#}" == find*unclassified* ]]; then echo unclassified_20261012_070000.jpg; fi
    if [[ "${!#}" == cd* ]]; then exit 1; fi;;
esac
exit 0
''')
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                    "ROOK_STAGING_DIR": str(self.staging), "ROOK_ARCHIVE_DIR": str(self.archive),
                    "ROOK_TEST_FAILURE": ""}

    def stub(self, name, contents):
        path = self.bin / name
        path.write_text(contents)
        path.chmod(0o755)

    def run_sync(self, failure=""):
        self.env["ROOK_TEST_FAILURE"] = failure
        result = subprocess.run(["bash", str(APP / "sync_archive.sh")], env=self.env,
                                capture_output=True, text=True, timeout=15)
        status = json.loads((self.staging / ".sync_status.json").read_text())
        return result, status

    def test_success_and_repeat_publish(self):
        for _ in range(2):
            result, status = self.run_sync()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(status["state"], "success")
            self.assertEqual(status["staged_classified_frames"], 1)
        self.assertEqual((self.archive / "classified/classified_20261012_071200.jpg").read_bytes(),
                         b"raw frame")

    def test_failed_copy_does_not_publish_partial_frame(self):
        self.stub("cp", '#!/bin/bash\nif [[ "$1" == *.jpg ]]; then echo partial > "$2"; exit 1; fi\nexec /bin/cp "$@"\n')
        result, status = self.run_sync()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(status["state"], "failed")
        self.assertNotIn("Sync complete", result.stdout)
        self.assertFalse((self.archive / "classified/classified_20261012_071200.jpg").exists())

    def test_failed_status_mirror_still_has_local_evidence(self):
        self.stub("cp", '#!/bin/bash\nif [[ "$1" == */.sync_status.json ]]; then exit 1; fi\nexec /bin/cp "$@"\n')
        result, status = self.run_sync()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(status["state"], "failed")

    def test_connection_listing_and_transfer_failures(self):
        for failure in ("connection", "listing", "transfer"):
            with self.subTest(failure=failure):
                result, status = self.run_sync(failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(status["state"], "failed")
                self.assertFalse((self.staging / "unclassified/unclassified_20261012_070000.jpg").exists())


if __name__ == "__main__":
    unittest.main()
