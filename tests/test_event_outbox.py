import copy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from rook_events import canonical, make_event, sha256, validate_event
from rook_outbox import Outbox, OutboxFull
from rook_event_worker import DeliveryError, HTTPSender, Worker, read_token


def event(image=None):
    return make_event('yard-pi', '2026-10-12T12:30:00Z',
                      {'name': 'stock-test', 'format': 'ncnn',
                       'artifact_sha256': 'a' * 64, 'classes_sha256': 'b' * 64},
                      [{'class_id': 7, 'label': 'truck', 'confidence': .9,
                        'xyxy': [.1, .2, .7, .8], 'subclass': None}], image=image)


def receipt(delivery):
    return {'schema_version': 1, 'device_id': delivery.event['device_id'],
            'event_id': delivery.event['event_id'], 'event_sha256': sha256(delivery.payload),
            'received_at': '2026-10-12T13:00:00Z'}


class EventTest(unittest.TestCase):
    def test_contract_rejects_bad_data_and_device_receive_time(self):
        for key, value in [('schema_version', True), ('schema_version', 2),
                           ('device_id', '../secret'), ('captured_at', '2026-10-12T12:00:00'),
                           ('received_at', '2026-10-12T12:00:00Z'), ('event_id', 'not-an-id')]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                e = event(); e[key] = value; validate_event(e)
        for key, value in [('class_id', True), ('class_id', 107), ('confidence', float('nan')),
                           ('confidence', float('inf')), ('xyxy', [.3, 0, .2, 1]),
                           ('xyxy', [0, 0, 2, 1]), ('label', '')]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                e = event(); e['detections'][0][key] = value; validate_event(e)
        e = event(); e['secret'] = 'must-not-travel'
        with self.assertRaises(ValueError): validate_event(e)

    def test_motion_and_optional_subclass_preserve_uncertainty(self):
        e = event(); e['detections'][0]['subclass'] = {
            'label': 'ups_truck', 'source': 'offline_candidate_v1', 'confidence': .51}
        validate_event(e)
        self.assertEqual(e['detections'][0]['label'], 'truck')
        e['detections'] = []
        with self.assertRaises(ValueError): validate_event(e)
        e['kind'] = 'motion'; validate_event(e)


class OutboxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'private/events.sqlite3'

    def tearDown(self):
        self.tmp.cleanup()

    def test_restart_preserves_image_and_idempotency(self):
        data = b'synthetic attachment bytes'
        e = event({'sha256': sha256(data), 'media_type': 'image/jpeg', 'byte_length': len(data),
                   'width': 640, 'height': 360, 'role': 'raw'})
        box = Outbox(self.db)
        self.assertTrue(box.enqueue(e, data, now=10))
        self.assertFalse(box.enqueue(e, data))
        reopened = Outbox(self.db)
        delivery = reopened.claim(now=11)
        self.assertEqual(delivery.image, data)
        self.assertEqual(delivery.event, e)
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)
        bad = copy.deepcopy(e); bad['detections'][0]['confidence'] = .8
        with self.assertRaises(ValueError): reopened.enqueue(bad, data)
        self.assertEqual(reopened.status()['items'], 1)

    def test_image_mismatch_is_never_committed(self):
        box = Outbox(self.db)
        e = event({'sha256': 'a' * 64, 'media_type': 'image/jpeg', 'byte_length': 3,
                   'width': 1, 'height': 1, 'role': 'raw'})
        with self.assertRaises(ValueError): box.enqueue(e, b'bad')
        with self.assertRaises(ValueError): box.enqueue(event(), b'unreferenced')
        self.assertEqual(box.status()['items'], 0)

    def test_bounds_reject_new_and_preserve_existing_and_persist_limits(self):
        box = Outbox(self.db, max_items=1, max_bytes=10000)
        first = event(); box.enqueue(first, now=1)
        with self.assertRaises(OutboxFull): box.enqueue(event(), now=2)
        reopened = Outbox(self.db)
        self.assertEqual(reopened.max_items, 1)
        with self.assertRaises(ValueError): Outbox(self.db, max_items=2)
        self.assertEqual(reopened.claim(now=2).event, first)
        other = Outbox(self.db.parent/'tiny.sqlite3', max_bytes=1)
        with self.assertRaises(OutboxFull): other.enqueue(event())
        self.assertEqual(other.status()['items'], 0)

    def test_crash_after_commit_survives_process_exit(self):
        e = event()
        program = ('import sys,json,os; from rook_outbox import Outbox; '
                   'Outbox(sys.argv[1]).enqueue(json.loads(sys.argv[2]),now=1); os._exit(23)')
        env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1]/'app')}
        result = subprocess.run([sys.executable, '-c', program, str(self.db), json.dumps(e)], env=env)
        self.assertEqual(result.returncode, 23)
        self.assertEqual(Outbox(self.db).claim(now=2).event, e)

    def test_concurrent_producers_and_workers_do_not_duplicate(self):
        box = Outbox(self.db); other = Outbox(self.db); e = event()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda q: q.enqueue(e, now=1), [box, other]))
        self.assertEqual(sorted(results), [False, True])
        with ThreadPoolExecutor(max_workers=2) as pool:
            deliveries = list(pool.map(lambda q: q.claim(now=2), [box, other]))
        self.assertEqual(sum(d is not None for d in deliveries), 1)

    def test_expired_lease_replays_same_id_and_stale_ack_cannot_delete(self):
        box = Outbox(self.db); e = event(); box.enqueue(e, now=0)
        first = box.claim(now=1, lease_seconds=10)
        self.assertIsNone(Outbox(self.db).claim(now=10))
        second = Outbox(self.db).claim(now=11)
        self.assertEqual(first.payload, second.payload)
        self.assertEqual(second.attempts, 2)
        self.assertFalse(box.acknowledge(first, receipt(first)))
        self.assertFalse(box.failed(first, 'network_error', now=12))
        self.assertTrue(box.acknowledge(second, receipt(second)))
        self.assertEqual(box.status()['items'], 0)

    def test_bad_receipt_cannot_delete(self):
        box = Outbox(self.db); box.enqueue(event(), now=0); delivery = box.claim(now=1)
        r = receipt(delivery); r['event_sha256'] = 'f' * 64
        with self.assertRaises(ValueError): box.acknowledge(delivery, r)
        self.assertEqual(box.status()['items'], 1)

    def test_offline_then_restart_replays_without_receiver_duplicate(self):
        box = Outbox(self.db); box.enqueue(event(), now=0)
        persisted_remote = {}
        class Receiver:
            def send(self, delivery):
                key = (delivery.event['device_id'], delivery.event['event_id'])
                first = key not in persisted_remote
                persisted_remote.setdefault(key, receipt(delivery))
                if first: raise DeliveryError('network_error')  # accepted, reply lost
                return persisted_remote[key]
        worker = Worker(box, Receiver(), clock=lambda: 1, jitter=lambda a,b: 1)
        self.assertEqual(worker.run_once(), 'retry')
        self.assertEqual(box.status()['items'], 1)
        restarted = Worker(Outbox(self.db), Receiver(), clock=lambda: 7, jitter=lambda a,b: 1)
        self.assertEqual(restarted.run_once(), 'delivered')
        self.assertEqual(len(persisted_remote), 1)
        self.assertEqual(box.status()['items'], 0)

    def test_auth_failure_blocks_preserves_and_can_be_explicitly_retried(self):
        box = Outbox(self.db); box.enqueue(event(), now=0)
        class Rejected:
            def send(self, delivery): raise DeliveryError('http_401', permanent=True, pause=True)
        self.assertEqual(Worker(box, Rejected(), clock=lambda: 1).run_once(), 'paused')
        self.assertEqual(box.status()['blocked'], 1)
        self.assertIsNone(box.claim(now=100))
        box.retry_blocked(now=101)
        self.assertIsNotNone(box.claim(now=101))

    def test_malformed_ack_retries_instead_of_deleting(self):
        box = Outbox(self.db); box.enqueue(event(), now=0)
        class Bad:
            def send(self, delivery): return {'ok': True}
        self.assertEqual(Worker(box, Bad(), clock=lambda: 1).run_once(), 'retry')
        self.assertEqual(box.status()['items'], 1)

    def test_bad_local_data_blocks_without_sending(self):
        class Never:
            def send(self, delivery): raise AssertionError('Must not send invalid data')
        for i, payload in enumerate((b'{}', b'not json', canonical(event()))):
            box = Outbox(self.db.parent/f'corrupt-{i}.sqlite3'); box.enqueue(event(), now=0)
            with sqlite3.connect(box.path) as db:
                db.execute('UPDATE events SET payload=?', (payload,))
            self.assertEqual(Worker(box, Never(), clock=lambda: 1).run_once(), 'blocked')
            self.assertEqual(box.status()['blocked'], 1)


class HTTPTest(unittest.TestCase):
    def test_https_only_no_redirect_and_bound_ack(self):
        import httpx
        for endpoint in ('http://example.test/ingest', 'https://user:pw@example.test/ingest',
                         'https://example.test/ingest?secret=x', 'https://example.test/#x'):
            with self.assertRaises(ValueError): HTTPSender(endpoint, 'x'*24)
        with tempfile.TemporaryDirectory() as tmp:
            box = Outbox(Path(tmp)/'queue.sqlite3'); box.enqueue(event(), now=0)
            delivery = box.claim(now=1)
            seen = []
            def handler(request):
                seen.append(request)
                return httpx.Response(307, headers={'Location': 'https://other.test/'})
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                sender = HTTPSender('https://example.test/ingest', 'x'*24, client)
                with self.assertRaises(DeliveryError) as exc: sender.send(delivery)
                self.assertEqual(exc.exception.code, 'http_307')
                self.assertEqual(len(seen), 1)
            def accepted(request):
                self.assertEqual(request.headers['Idempotency-Key'], 'yard-pi/'+delivery.event['event_id'])
                wire = json.loads(request.content)
                self.assertEqual(wire['event_json'].encode('utf-8'), delivery.payload)
                self.assertEqual(json.loads(wire['event_json']), delivery.event)
                return httpx.Response(201, json=receipt(delivery))
            with httpx.Client(transport=httpx.MockTransport(accepted)) as client:
                self.assertEqual(HTTPSender('https://example.test/ingest', 'x'*24, client).send(delivery), receipt(delivery))
            for body in (b'{}', b'x'*4097):
                with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=body))) as client:
                    with self.assertRaises(DeliveryError):
                        HTTPSender('https://example.test/ingest', 'x'*24, client).send(delivery)

    def test_token_file_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'device-token'; path.write_text('x'*24)
            path.chmod(0o644)
            with self.assertRaises(ValueError): read_token(path)
            path.chmod(0o600)
            self.assertEqual(read_token(path), 'x'*24)

    def test_network_retry_and_permanent_status_classification(self):
        import httpx
        with tempfile.TemporaryDirectory() as tmp:
            box = Outbox(Path(tmp)/'queue.sqlite3'); box.enqueue(event(), now=0)
            delivery = box.claim(now=1)
            for status, permanent, pause in ((429, False, False), (503, False, False),
                                              (401, True, True), (403, True, True),
                                              (409, True, False), (422, True, False)):
                with self.subTest(status=status):
                    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status))) as client:
                        with self.assertRaises(DeliveryError) as exc:
                            HTTPSender('https://example.test/ingest', 'x'*24, client).send(delivery)
                        self.assertEqual(exc.exception.permanent, permanent)
                        self.assertEqual(exc.exception.pause, pause)
            def timeout(request):
                raise httpx.ReadTimeout('sensitive provider detail', request=request)
            with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
                with self.assertRaises(DeliveryError) as exc:
                    HTTPSender('https://example.test/ingest', 'x'*24, client).send(delivery)
                self.assertEqual(str(exc.exception), 'network_error')


if __name__ == '__main__':
    unittest.main()
