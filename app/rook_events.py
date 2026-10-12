"""Version 1 device observations. No camera, ML, credentials or network imports."""
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import uuid

MAX_EVENT_BYTES = 64 * 1024
MAX_IMAGE_BYTES = 2 * 1024 * 1024


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _keys(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected.split()):
        raise ValueError('Unexpected event fields')


def _text(value, maximum=128):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(c) < 32 for c in value)):
        raise ValueError('Invalid text field')


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('Expected SHA256')


def timestamp(value):
    if not isinstance(value, str) or not value.endswith('Z') or 'T' not in value:
        raise ValueError('Expected UTC timestamp ending in Z')
    parsed = datetime.fromisoformat(value[:-1] + '+00:00')
    if parsed.utcoffset().total_seconds() != 0:
        raise ValueError('Expected UTC timestamp')
    return parsed


def _unit(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('Expected finite normalized number')


def validate_event(event):
    _keys(event, 'schema_version event_id device_id kind captured_at received_at model detections image')
    if type(event['schema_version']) is not int or event['schema_version'] != 1:
        raise ValueError('Unsupported event version')
    if not isinstance(event['event_id'], str) or str(uuid.UUID(event['event_id'])) != event['event_id']:
        raise ValueError('Expected canonical UUID event ID')
    device = event['device_id']
    if not isinstance(device, str) or not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', device):
        raise ValueError('Invalid device ID')
    if event['kind'] not in ('detection', 'motion'):
        raise ValueError('Unsupported observation kind')
    timestamp(event['captured_at'])
    # Receiver time belongs to the server receipt, never to the device clock.
    if event['received_at'] is not None:
        raise ValueError('Device cannot set received_at')
    model = event['model']
    _keys(model, 'name format artifact_sha256 classes_sha256')
    _text(model['name'])
    if model['format'] not in ('ncnn', 'pytorch', 'onnx'):
        raise ValueError('Unsupported model format')
    _hash(model['artifact_sha256'])
    _hash(model['classes_sha256'])
    detections = event['detections']
    if not isinstance(detections, list) or len(detections) > 200:
        raise ValueError('Too many detections')
    if (event['kind'] == 'detection') != bool(detections):
        raise ValueError('Observation kind and detections disagree')
    for box in detections:
        _keys(box, 'class_id label confidence xyxy subclass')
        if type(box['class_id']) is not int or not 0 <= box['class_id'] <= 109 or box['class_id'] == 107:
            raise ValueError('Invalid or retired detector class ID')
        _text(box['label'], 64)
        _unit(box['confidence'])
        coords = box['xyxy']
        if not isinstance(coords, list) or len(coords) != 4:
            raise ValueError('Expected normalized xyxy box')
        for v in coords:
            _unit(v)
        if coords[0] >= coords[2] or coords[1] >= coords[3]:
            raise ValueError('Box has no area')
        if box['subclass'] is not None:
            sub = box['subclass']
            _keys(sub, 'label source confidence')
            _text(sub['label'], 64)
            _text(sub['source'])
            _unit(sub['confidence'])
    image = event['image']
    if image is not None:
        _keys(image, 'sha256 media_type byte_length width height role')
        _hash(image['sha256'])
        if image['media_type'] != 'image/jpeg' or image['role'] != 'raw':
            raise ValueError('Only raw JPEG attachments are supported')
        if type(image['byte_length']) is not int or not 0 < image['byte_length'] <= MAX_IMAGE_BYTES:
            raise ValueError('Image is too large')
        for key in ('width', 'height'):
            if type(image[key]) is not int or not 0 < image[key] <= 16384:
                raise ValueError('Invalid image dimensions')
    data = canonical(event)
    if len(data) > MAX_EVENT_BYTES:
        raise ValueError('Event is too large')
    return data


def validate_attachment(event, image):
    reference = event['image']
    if reference is None:
        if image is not None:
            raise ValueError('Unreferenced image bytes')
    elif (not isinstance(image, bytes) or len(image) != reference['byte_length']
          or sha256(image) != reference['sha256']):
        raise ValueError('Image bytes do not match their reference')


def make_event(device_id, captured_at, model, detections, image=None, event_id=None):
    event = dict(schema_version=1, event_id=event_id or str(uuid.uuid4()),
                 device_id=device_id, kind='detection' if detections else 'motion',
                 captured_at=captured_at, received_at=None, model=model,
                 detections=detections, image=image)
    validate_event(event)
    # Snapshot caller-owned lists/dicts before handing them to another component.
    return json.loads(canonical(event))


def validate_receipt(receipt, event, event_sha256):
    _keys(receipt, 'schema_version device_id event_id event_sha256 received_at')
    if type(receipt['schema_version']) is not int or receipt['schema_version'] != 1:
        raise ValueError('Unsupported receipt version')
    for key in ('device_id', 'event_id'):
        if receipt[key] != event[key]:
            raise ValueError('Receipt identity mismatch')
    if receipt['event_sha256'] != event_sha256:
        raise ValueError('Receipt payload mismatch')
    timestamp(receipt['received_at'])
