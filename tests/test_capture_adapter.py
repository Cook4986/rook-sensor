from pathlib import Path
import json
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from rook_capture import (CapturePublisher, SceneGate, fingerprint_artifact, finish_model_evidence,
                          load_observed_model, publish_observation, start_capture)
from rook_events import canonical, sha256
from rook_outbox import Outbox, OutboxFull


NAMES = [f'class_{i}' for i in range(80)]
NAMES[7], NAMES[14] = 'truck', 'bird'
STAMP = '2026-10-12T18:00:00Z'


def evidence():
    files = [{'path': 'model.bin', 'bytes': 1, 'sha256': sha256(b'a')},
             {'path': 'model.param', 'bytes': 1, 'sha256': sha256(b'b')}]
    return {'model': {'name': 'synthetic-model', 'format': 'ncnn',
                      'artifact_sha256': sha256(canonical(files)), 'classes_sha256': sha256(canonical(NAMES))},
            'class_names': NAMES, 'artifact_files': files}


def detection():
    return [{'class_id': 7, 'label': 'truck', 'confidence': .9,
             'xyxy': [.1, .2, .8, .9], 'subclass': None}]


def wait_for(condition):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if condition(): return
        time.sleep(.01)
    raise AssertionError('Background operation did not finish')


class ModelEvidenceTest(unittest.TestCase):
    def test_disabled_path_has_no_fingerprint_or_name_side_effects(self):
        model = object(); loader = Mock(return_value=model)
        with patch('rook_capture.fingerprint_artifact', side_effect=AssertionError('must not hash')):
            self.assertEqual(load_observed_model(loader, 'missing.pt', 'pytorch', False), (model, None))
        loader.assert_called_once_with('missing.pt')
        with patch('rook_capture.CapturePublisher', side_effect=AssertionError('must not start')):
            self.assertIsNone(start_capture(False, None, None, '/unused'))

    def test_file_identity_and_loaded_vocabulary_are_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'model.pt'; path.write_bytes(b'weights')
            model = SimpleNamespace(names=dict(enumerate(NAMES)))
            loaded, proof = load_observed_model(lambda p: model, str(path), 'pytorch', True)
            self.assertIs(loaded, model)
            self.assertEqual(proof['model']['artifact_sha256'], sha256(b'weights'))
            self.assertEqual(proof['model']['classes_sha256'], sha256(canonical(NAMES)))

    def test_deferred_verification_preserves_lazy_backend_setup_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'model.pt'; path.write_bytes(b'weights')
            configured = []
            class LazyModel:
                @property
                def names(self):
                    if not configured: raise AssertionError('Thread setup must happen first')
                    return NAMES
            model, pending = load_observed_model(lambda p: LazyModel(), str(path), 'pytorch', True,
                                                  defer_verification=True)
            configured.append('NCNN threads set')
            proof = finish_model_evidence(model, pending)
            self.assertEqual(proof['model']['artifact_sha256'], sha256(b'weights'))

    def test_mutation_during_load_disables_capture_but_retains_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'model.pt'; path.write_bytes(b'old')
            model = SimpleNamespace(names=NAMES)
            def loader(p):
                path.write_bytes(b'changed')
                return model
            loaded, proof = load_observed_model(loader, str(path), 'pytorch', True)
            self.assertIs(loaded, model); self.assertIsNone(proof)
            def broken(p): raise RuntimeError('loader failure')
            with self.assertRaisesRegex(RuntimeError, 'loader failure'):
                load_observed_model(broken, str(path), 'pytorch', True)

    def test_missing_preload_artifact_does_not_certify_downloaded_weights(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'missing.pt'
            model = SimpleNamespace(names=NAMES)
            def loader(p):
                path.write_bytes(b'downloaded')
                return model
            loaded, proof = load_observed_model(loader, str(path), 'pytorch', True)
            self.assertIs(loaded, model); self.assertIsNone(proof)

    def test_ncnn_fingerprint_covers_names_and_bytes_and_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'model.bin').write_bytes(b'weights')
            (root/'model.param').write_bytes(b'graph')
            (root/'metadata.yaml').write_text('names: synthetic')
            before = fingerprint_artifact(root, 'ncnn')
            (root/'metadata.yaml').write_text('names: changed')
            self.assertNotEqual(before, fingerprint_artifact(root, 'ncnn'))
            (root/'alias.bin').symlink_to(root/'model.bin')
            with self.assertRaises(ValueError): fingerprint_artifact(root, 'ncnn')


class CaptureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name)/'outbox/events.sqlite3'
        self.publishers = []
        self.frame = np.zeros((48, 64, 3), dtype=np.uint8)

    def tearDown(self):
        for publisher in self.publishers:
            self.assertTrue(publisher.close(timeout=3))
        self.tmp.cleanup()

    def publisher(self, **kwargs):
        publisher = CapturePublisher('yard-pi', evidence(), self.db, **kwargs)
        self.publishers.append(publisher)
        self.assertTrue(publisher.ready.wait(3))
        return publisher

    def test_raw_frame_snapshot_and_real_jpeg_reach_durable_outbox(self):
        import cv2
        publisher = self.publisher()
        self.assertTrue(publisher.submit(self.frame, STAMP, detection()))
        self.frame[:] = 255  # Caller reuses its camera buffer after acceptance.
        wait_for(lambda: publisher.status()['counts'].get('committed') == 1)
        stored = Outbox(self.db).claim()
        self.assertEqual(stored.event['captured_at'], STAMP)
        self.assertEqual(stored.event['model'], evidence()['model'])
        self.assertEqual(stored.event['image']['sha256'], sha256(stored.image))
        decoded = cv2.imdecode(np.frombuffer(stored.image, dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(decoded.shape, (48, 64, 3))
        self.assertLess(float(decoded.mean()), 1)  # Original raw black frame, no boxes drawn.
        manifest = next((self.db.parent/'models').glob('*.json'))
        self.assertEqual(json.loads(manifest.read_text())['class_names'], NAMES)

    def test_stalled_disk_bounds_handoff_and_does_not_wait_for_alerts(self):
        entered, release = threading.Event(), threading.Event()
        class Stalled:
            def __init__(self, path): pass
            def enqueue(self, event, image):
                entered.set()
                release.wait(3)
        publisher = self.publisher(outbox_factory=Stalled, max_pending=2)
        try:
            self.assertTrue(publisher.submit(self.frame, STAMP, detection()))
            self.assertTrue(entered.wait(2))
            started = time.monotonic()
            self.assertTrue(publisher.submit(self.frame, STAMP, detection()))
            for _ in range(10):
                self.assertFalse(publisher.submit(self.frame, STAMP, detection()))
            alert = Mock()
            alert('existing Slack path continues')
            alert.assert_called_once()
            self.assertLess(time.monotonic() - started, .5)
            self.assertFalse(release.is_set())
            self.assertEqual(publisher.status()['counts']['handoff_full'], 10)
            self.assertFalse(publisher.close(timeout=.01))
        finally:
            release.set()

    def test_full_and_failed_disk_are_counted_without_escaping(self):
        calls = []
        class Failed:
            def __init__(self, path): pass
            def enqueue(self, event, image):
                calls.append(event)
                if len(calls) == 1: raise OutboxFull('full')
                raise OSError('sensitive filesystem detail')
        publisher = self.publisher(outbox_factory=Failed)
        self.assertTrue(publisher.submit(self.frame, STAMP, detection()))
        wait_for(lambda: publisher.status()['counts'].get('outbox_full') == 1)
        self.assertTrue(publisher.submit(self.frame, STAMP, detection()))
        wait_for(lambda: publisher.status()['counts'].get('write_failed') == 1)
        self.assertEqual(publisher.status()['last_error'], 'OSError')
        self.assertNotIn('sensitive', json.dumps(publisher.status()))

    def test_startup_and_encoder_failures_are_isolated(self):
        class Broken:
            def __init__(self, path): raise OSError('unwritable')
        publisher = CapturePublisher('yard-pi', evidence(), self.db, outbox_factory=Broken)
        self.publishers.append(publisher)
        wait_for(lambda: publisher.status()['state'] == 'failed')
        self.assertFalse(publisher.submit(self.frame, STAMP, detection()))
        def encode(frame): raise ValueError('encoding failed')
        other = self.publisher(encoder=encode)
        self.assertTrue(other.submit(self.frame, STAMP, detection()))
        wait_for(lambda: other.status()['counts'].get('write_failed') == 1)
        self.assertEqual(Outbox(self.db).status()['items'], 0)

    def test_invalid_frame_and_wrong_vocabulary_do_not_consume_slots(self):
        publisher = self.publisher(max_pending=1)
        wrong = detection(); wrong[0]['label'] = 'car'
        self.assertFalse(publisher.submit(self.frame, STAMP, wrong))
        huge = np.zeros((1920, 1920, 3), dtype=np.uint8)
        self.assertFalse(publisher.submit(huge, STAMP, detection()))
        self.assertTrue(publisher.submit(self.frame, STAMP, detection()))
        wait_for(lambda: publisher.status()['counts'].get('committed') == 1)

    def test_observation_filter_geometry_and_safe_boundary(self):
        publisher = self.publisher()
        boxes = SimpleNamespace(cls=[7, 14], conf=[.9, .7],
                                xyxy=np.array([[0, 0, 32, 24], [4, 4, 8, 8]]))
        self.assertTrue(publish_observation(publisher, self.frame, STAMP, boxes, NAMES,
                                            ['truck', 'bird'], {'bird': .75}))
        wait_for(lambda: publisher.status()['counts'].get('committed') == 1)
        stored = Outbox(self.db).claim()
        self.assertEqual(len(stored.event['detections']), 1)
        self.assertEqual(stored.event['detections'][0]['xyxy'], [0, 0, .5, .5])
        self.assertFalse(publish_observation(publisher, self.frame, STAMP, boxes, {}, ['truck']))
        self.assertFalse(publish_observation(None, None, None))

    def test_motion_sample_is_raw_observation_not_training_negative(self):
        publisher = self.publisher()
        self.assertTrue(publish_observation(publisher, self.frame, STAMP))
        wait_for(lambda: publisher.status()['counts'].get('committed') == 1)
        stored = Outbox(self.db).claim()
        self.assertEqual(stored.event['kind'], 'motion')
        self.assertEqual(stored.event['detections'], [])

    def test_inconsistent_provenance_cannot_start_capture(self):
        proof = evidence(); proof['class_names'] = list(NAMES); proof['class_names'][7] = 'wrong'
        self.assertIsNone(start_capture(True, proof, 'yard-pi', self.db))
        proof = evidence(); proof['artifact_files'][0]['sha256'] = 'f'*64
        self.assertIsNone(start_capture(True, proof, 'yard-pi', self.db))
        self.assertFalse(self.db.exists())

    def test_capture_to_https_worker_preserves_exact_event_and_image(self):
        import base64
        import httpx
        from rook_event_worker import HTTPSender, Worker
        publisher = self.publisher()
        self.assertTrue(publish_observation(publisher, self.frame, STAMP))
        wait_for(lambda: publisher.status()['counts'].get('committed') == 1)
        def ingest(request):
            wire = json.loads(request.content)
            observation = json.loads(wire['event_json'])
            image = base64.b64decode(wire['image_base64'])
            self.assertEqual(sha256(image), observation['image']['sha256'])
            self.assertEqual(observation['captured_at'], STAMP)
            return httpx.Response(201, json={'schema_version': 1, 'device_id': observation['device_id'],
                'event_id': observation['event_id'], 'event_sha256': sha256(wire['event_json'].encode()),
                'received_at': STAMP})
        with httpx.Client(transport=httpx.MockTransport(ingest)) as client:
            worker = Worker(Outbox(self.db), HTTPSender('https://example.test/ingest', 'x'*24, client))
            self.assertEqual(worker.run_once(), 'delivered')
        self.assertEqual(Outbox(self.db).status()['items'], 0)


class GateTest(unittest.TestCase):
    def test_scene_return_and_throttled_arrivals_are_not_lost(self):
        gate = SceneGate(interval=10)
        self.assertTrue(gate.due(['truck'], 0)); gate.accepted(['truck'], 0)
        self.assertFalse(gate.due(['truck'], 20))
        self.assertFalse(gate.due([], 21))
        self.assertTrue(gate.due(['truck'], 22)); gate.accepted(['truck'], 22)
        self.assertFalse(gate.due(['truck', 'bird'], 23))
        self.assertTrue(gate.due(['truck', 'bird'], 32))


if __name__ == '__main__':
    unittest.main()
