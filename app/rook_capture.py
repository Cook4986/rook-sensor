"""Opt-in, bounded camera handoff. Disk/encoding stay on one daemon thread."""
from collections import Counter
import hashlib
import json
import logging
import os
from pathlib import Path
import queue
import tempfile
import threading
import time

from rook_events import MAX_IMAGE_BYTES, canonical, make_event, sha256, utc_now
from rook_outbox import Outbox, OutboxFull

MAX_FRAME_BYTES = 8 * 1024 * 1024


class SceneGate:
    """Label arrivals, not object tracking or certified independent visits."""
    def __init__(self, interval=10):
        self.interval, self.last = interval, float('-inf')
        self.reported = set()

    def due(self, labels, now):
        self.reported.intersection_update(labels)
        return bool(set(labels) - self.reported) and now - self.last >= self.interval

    def accepted(self, labels, now):
        self.reported = set(labels)
        self.last = now


def _local(path):
    path = Path(path).expanduser()
    if path.is_symlink():
        raise ValueError('Symlink artifact is not supported')
    path = path.resolve(strict=True)
    if any(part in ('Dropbox', 'CloudStorage') for part in path.parts):
        raise ValueError('Model hashing requires a local artifact')
    return path


def fingerprint_artifact(path, model_format):
    """Single weights file: byte SHA. NCNN: SHA of a sorted relative-file manifest."""
    path = _local(path)
    is_dir = path.is_dir()
    if (model_format == 'ncnn') != is_dir:
        raise ValueError('Model format/path mismatch')

    def files():
        entries = sorted(path.rglob('*')) if is_dir else [path]
        if any(p.is_symlink() for p in entries):
            raise ValueError('Model artifacts cannot contain symlinks')
        return [p for p in entries if p.is_file()]

    selected = files()
    if not selected or len(selected) > 1000:
        raise ValueError('Invalid model artifact file count')
    if is_dir and not {'.bin', '.param'}.issubset({p.suffix for p in selected}):
        raise ValueError('NCNN weights and graph are required')
    manifest, total = [], 0
    for file in selected:
        before = file.stat()
        total += before.st_size
        if total > 512 * 1024 * 1024:
            raise ValueError('Model artifact exceeds fingerprint bound')
        digest = hashlib.sha256()
        with file.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        after = file.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('Model artifact changed while hashing')
        manifest.append({'path': str(file.relative_to(path)) if is_dir else file.name,
                         'bytes': before.st_size, 'sha256': digest.hexdigest()})
    if selected != files():
        raise ValueError('Model file list changed while hashing')
    return {'artifact_sha256': sha256(canonical(manifest)) if is_dir else manifest[0]['sha256'],
            'files': manifest}


def load_observed_model(loader, path, model_format, enabled=False, defer_verification=False):
    """Preserve loader failures/fallbacks; provenance failures disable only capture.

    Hash on both sides of model construction and names resolution (which can
    initialize lazy backends). Never claim a post-load file alone proves identity.
    """
    before = None
    if enabled:
        try:
            before = fingerprint_artifact(path, model_format)
        except Exception as error:
            logging.warning('Event provenance unavailable before load (%s)', type(error).__name__)
    model = loader(path)
    if before is None:
        return model, None
    pending = {'path': path, 'format': model_format, 'before': before}
    # NCNN thread configuration must retain its original position before lazy
    # backend initialization via model.names in the engine.
    return model, pending if defer_verification else finish_model_evidence(model, pending)


def finish_model_evidence(model, pending):
    if pending is None:
        return None
    try:
        path, model_format, before = pending['path'], pending['format'], pending['before']
        names = model.names
        names = [names[i] for i in range(len(names))]
        if len(names) not in (80, 110) or any(not isinstance(n, str) or not n or len(n) > 64 for n in names):
            raise ValueError('Unexpected model vocabulary')
        after = fingerprint_artifact(path, model_format)
        if before != after:
            raise ValueError('Model artifact changed during loading')
        identity = {'name': Path(path).name, 'format': model_format,
                    'artifact_sha256': before['artifact_sha256'], 'classes_sha256': sha256(canonical(names))}
        return {'model': identity, 'class_names': names, 'artifact_files': before['files']}
    except Exception as error:
        logging.warning('Event provenance unavailable after load (%s)', type(error).__name__)
        return None


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(canonical(value)); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def encode_raw(frame):
    import cv2
    ok, image = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise ValueError('JPEG encoding failed')
    content = image.tobytes()
    if len(content) > MAX_IMAGE_BYTES:
        raise ValueError('Encoded frame exceeds attachment bound')
    return content


class CapturePublisher:
    def __init__(self, device_id, evidence, db_path, max_pending=2,
                 outbox_factory=Outbox, encoder=encode_raw):
        if type(max_pending) is not int or not 1 <= max_pending <= 8:
            raise ValueError('Invalid handoff capacity')
        self.evidence = json.loads(canonical(evidence))
        self.model = self.evidence['model']
        make_event(device_id, utc_now(), self.model, [])  # Validate before launching.
        names, files = self.evidence['class_names'], self.evidence['artifact_files']
        if sha256(canonical(names)) != self.model['classes_sha256']:
            raise ValueError('Vocabulary manifest differs from model identity')
        artifact_hash = (sha256(canonical(files)) if self.model['format'] == 'ncnn'
                         else files[0]['sha256'] if len(files) == 1 else None)
        if not files or artifact_hash != self.model['artifact_sha256']:
            raise ValueError('Artifact manifest differs from model identity')
        self.device_id, self.db_path = device_id, Path(db_path).expanduser().resolve()
        if any(p in ('Dropbox', 'CloudStorage') for p in self.db_path.parts):
            raise ValueError('Capture state requires local disk')
        self.encoder, self.outbox_factory = encoder, outbox_factory
        self._queue = queue.Queue(max_pending)
        self._slots = threading.BoundedSemaphore(max_pending)  # Includes in-flight work.
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._counts = Counter()
        self._state, self._last_error = 'starting', None
        self._last_health, self._health_at = None, float('-inf')
        self.scene = SceneGate()
        self.ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name='rook-capture', daemon=True)
        self._thread.start()

    def _record(self, key, error=None):
        with self._lock:
            self._counts[key] += 1
            if error:
                self._last_error = error

    def status(self):
        with self._lock:
            return {'state': self._state, 'counts': dict(self._counts), 'last_error': self._last_error}

    def _health(self):
        try:
            state = self.status()
            elapsed = time.monotonic() - self._health_at
            if elapsed < 5 and state['state'] == (self._last_health or {}).get('state'):
                return
            if state == self._last_health and elapsed < 60:
                return
            _atomic_json(self.db_path.parent/'capture_status.json', {**state, 'updated_at': utc_now()})
            self._last_health, self._health_at = state, time.monotonic()
        except Exception:
            self._record('health_write_failed', 'health_write_failed')

    def submit(self, frame, captured_at, detections):
        """No disk/encoding/network; only a bounded frame copy and metadata snapshot.

        True means accepted in memory, NOT durable. Failures never escape to camera.
        """
        if not self.ready.is_set() or self._stop.is_set():
            self._record('not_ready')
            return False
        if not self._slots.acquire(blocking=False):
            self._record('handoff_full')
            return False
        try:
            if (len(frame.shape) != 3 or frame.shape[2] != 3 or frame.dtype.name != 'uint8'
                    or not 0 < frame.nbytes <= MAX_FRAME_BYTES):
                raise ValueError('Expected bounded uint8 BGR frame')
            event = make_event(self.device_id, captured_at, self.model, detections)
            for box in event['detections']:
                if self.evidence['class_names'][box['class_id']] != box['label']:
                    raise ValueError('Detection differs from loaded vocabulary')
            copied = frame.copy()
            with self._lock:
                if self._stop.is_set():
                    raise ValueError('Capture is stopping')
                self._queue.put_nowait((event, copied))
            self._record('accepted_memory')
            return True
        except Exception:
            self._record('invalid_capture', 'invalid_capture')
            self._slots.release()
            return False

    def _run(self):
        try:
            outbox = self.outbox_factory(self.db_path)
            key = self.model['artifact_sha256'] + '-' + self.model['classes_sha256']
            _atomic_json(self.db_path.parent/'models'/(key+'.json'), self.evidence)
            with self._lock:
                self._state = 'ready'
            self.ready.set()
        except Exception as error:
            with self._lock:
                self._state = 'failed'
            self._record('startup_failed', type(error).__name__)
            self._health()
            return
        self._health()
        while not self._stop.is_set() or not self._queue.empty():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                self._health()
                continue
            if item is None:
                self._queue.task_done()
                break
            event, frame = item
            del item
            content = None
            try:
                content = self.encoder(frame)
                event['image'] = {'sha256': sha256(content), 'byte_length': len(content),
                                  'media_type': 'image/jpeg', 'width': int(frame.shape[1]),
                                  'height': int(frame.shape[0]), 'role': 'raw'}
                outbox.enqueue(event, content)
                self._record('committed')
            except OutboxFull:
                self._record('outbox_full', 'outbox_full')
            except Exception as error:
                self._record('write_failed', type(error).__name__)
            finally:
                del frame, event, content
                self._slots.release()
                self._queue.task_done()
                self._health()
        self.ready.clear()
        with self._lock:
            self._state = 'stopped'
        self._health()

    def close(self, timeout=0.25):
        with self._lock:
            self._stop.set()
            try:
                self._queue.put_nowait(None)  # Wake an idle worker without waiting.
            except queue.Full:
                pass  # Pending frames will drain before the loop exits.
        self._thread.join(timeout)
        return not self._thread.is_alive()


def start_capture(enabled, evidence, device_id, db_path):
    if not enabled:
        return None
    try:
        if evidence is None:
            raise ValueError('Verified loaded-model evidence required')
        return CapturePublisher(device_id, evidence, db_path)
    except Exception as error:
        logging.warning('Event capture disabled (%s); detection/alerts continue', type(error).__name__)
        return None


def publish_observation(publisher, frame, captured_at, boxes=None, names=None,
                        eligible_labels=None, minimum_confidence=None):
    """Safe engine boundary; never infer generic parents or copy annotated pixels."""
    if publisher is None:
        return False
    try:
        if not publisher.ready.is_set():
            return False
        detections = []
        if boxes is not None:
            if len(boxes.cls) > 200:
                raise ValueError('Too many boxes')
            height, width = frame.shape[:2]
            for i, cls in enumerate(boxes.cls):
                cls = int(cls); label = names[cls]; confidence = float(boxes.conf[i])
                if eligible_labels is not None and label not in eligible_labels:
                    continue
                if confidence < (minimum_confidence or {}).get(label, 0):
                    continue
                xyxy = boxes.xyxy[i].tolist()
                coords = [v / (width if j % 2 == 0 else height) for j, v in enumerate(xyxy)]
                detections.append({'class_id': cls, 'label': label, 'confidence': confidence,
                                   'xyxy': coords, 'subclass': None})
            if not detections:
                publisher.scene.due([], time.monotonic())
                return False  # A filtered detection is not an empty-frame claim.
            labels = {d['label'] for d in detections}
            now = time.monotonic()
            if not publisher.scene.due(labels, now):
                return False
            accepted = publisher.submit(frame, captured_at, detections)
            if accepted:
                publisher.scene.accepted(labels, now)
            return accepted
        return publisher.submit(frame, captured_at, detections)
    except Exception:
        try:
            publisher._record('invalid_capture', 'invalid_capture')
        except Exception:
            pass
        return False


def stop_capture(publisher):
    if publisher is not None:
        try:
            return publisher.close()
        except Exception:
            return False
    return True
