import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from archive_review import make_server


class ReviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.item = {"kind": "cached_candidate", "cache_hash": "test-hash",
                     "label": "ups_truck", "source_filename": "a.jpg",
                     "source_paths": [str(self.root / "a.jpg")]}
        (self.root / "a.jpg").write_bytes(b"test image")
        (self.root / "review_batch.json").write_text(json.dumps([self.item]))
        (self.root / "discovery_batch.json").write_text("[]")
        self.server, self.url = make_server(self.root, self.root, self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        self.cookie = ""

    def tearDown(self):
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method, path, body=None, **headers):
        self.connection.request(method, path, body, headers={"Cookie": self.cookie, **headers})
        response = self.connection.getresponse()
        data = response.read()
        return response, data

    def login(self):
        response, _ = self.request("GET", "/?" + urlparse(self.url).query)
        self.assertEqual(response.status, 303)
        self.cookie = response.getheader("Set-Cookie").split(";")[0]

    def test_private_routes_and_host_validation(self):
        for route in ("/", "/api/items", "/image?item=test-hash&view=original"):
            response, _ = self.request("GET", route)
            self.assertEqual(response.status, 403)
        self.login()
        response, body = self.request("GET", "/api/items")
        self.assertEqual(response.status, 200)
        self.assertEqual(len(json.loads(body)["items"]), 1)
        response, _ = self.request("GET", "/api/items", Host="attacker.example")
        self.assertEqual(response.status, 403)

    def test_decision_requires_origin_and_is_append_only_idempotent(self):
        self.login()
        payload = json.dumps({"item_id": "test-hash", "decision": "approve"})
        response, _ = self.request("POST", "/api/decisions", payload,
                                  **{"Content-Type": "application/json", "Origin": "https://other.example"})
        self.assertEqual(response.status, 403)
        ledger = self.root / "review_decisions.jsonl"
        self.assertFalse(ledger.exists())
        for _ in range(2):
            response, _ = self.request("POST", "/api/decisions", payload,
                                      **{"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.server.server_port}"})
            self.assertEqual(response.status, 200)
        self.assertEqual(len(ledger.read_text().splitlines()), 1)
        record = json.loads(ledger.read_text())
        self.assertFalse(record["applied_to_training"])
        self.assertEqual((self.root / "a.jpg").read_bytes(), b"test image")

    def test_image_route_cannot_read_arbitrary_files(self):
        self.login()
        response, _ = self.request("GET", "/image?item=../../etc/passwd&view=original")
        self.assertEqual(response.status, 404)
        response, body = self.request("GET", "/image?item=test-hash&view=original")
        self.assertEqual(response.status, 200)
        self.assertEqual(body, b"test image")


if __name__ == "__main__":
    unittest.main()
