#!/usr/bin/env python3
"""Prepare local raw-image snapshots and record explicit human box approvals.

No paid inference, legacy cache edits or automatic training approval. Teacher model
proposals are optional, run only against local snapshots, and are never truth.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from PIL import Image
from archive_catalog import require_local
from train_custom_model import CUSTOM_CLASSES

COCO = ('person bicycle car motorcycle airplane bus train truck boat traffic_light '
        'fire_hydrant stop_sign parking_meter bench bird cat dog horse sheep cow '
        'elephant bear zebra giraffe backpack umbrella handbag tie suitcase frisbee '
        'skis snowboard sports_ball kite baseball_bat baseball_glove skateboard '
        'surfboard tennis_racket bottle wine_glass cup fork knife spoon bowl banana '
        'apple sandwich orange broccoli carrot hot_dog pizza donut cake chair couch '
        'potted_plant bed dining_table toilet tv laptop mouse remote keyboard cell_phone '
        'microwave oven toaster sink refrigerator book clock vase scissors teddy_bear '
        'hair_drier toothbrush').split()
CLASSES = COCO + CUSTOM_CLASSES


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def normalize_boxes(boxes):
    if not isinstance(boxes, list) or len(boxes) > 100:
        raise ValueError('Expected at most 100 boxes')
    result = []
    for box in boxes:
        cls, coords = box['class_id'], box['xyxy']
        if type(cls) is not int or not 0 <= cls < len(CLASSES) or CLASSES[cls] == 'flood':
            raise ValueError('Invalid or retired class')
        if not isinstance(coords, list) or len(coords) != 4 or any(
            type(v) not in (int, float) or not math.isfinite(v) for v in coords):
            raise ValueError('Invalid box coordinates')
        x1, y1, x2, y2 = [round(v, 6) for v in coords]
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise ValueError('Boxes must have positive area and remain inside the frame')
        result.append({'class_id': cls, 'xyxy': [x1, y1, x2, y2]})
    result.sort(key=lambda b: (b['class_id'], b['xyxy']))
    if len({canonical(b) for b in result}) != len(result):
        raise ValueError('Duplicate boxes')
    return result


def label_bytes(boxes):
    lines = []
    for b in normalize_boxes(boxes):
        x1, y1, x2, y2 = b['xyxy']
        lines.append(f"{b['class_id']} {(x1+x2)/2:.6f} {(y1+y2)/2:.6f} {x2-x1:.6f} {y2-y1:.6f}\n")
    return ''.join(lines).encode()


def verified_image(item):
    path = require_local(item['source_paths'][0])
    content = path.read_bytes()
    if digest(content) != item['source_sha256']:
        raise ValueError('Source image changed; regenerate the review package')
    return content


def append_box_decision(path, item, data):
    if data.get('source_sha256') != item['source_sha256'] or data.get('proposal_sha256') != item['proposal_sha256']:
        raise ValueError('Stale review; reload the exact source and proposals')
    verified_image(item)
    decision = data.get('decision')
    if decision not in ('approve_boxes', 'empty_frame', 'defer'):
        raise ValueError('Invalid box-review decision')
    approved = decision != 'defer'
    boxes = normalize_boxes(data.get('boxes', []))
    if approved and data.get('complete_frame') is not True:
        raise ValueError('Confirm all visible target objects were checked before approval')
    if decision == 'approve_boxes' and not boxes:
        raise ValueError('Draw boxes or explicitly confirm an empty frame')
    if decision == 'empty_frame' and boxes:
        raise ValueError('An empty frame cannot have boxes')
    record = {'schema_version': 1, 'item_id': item['item_id'], 'scope': 'frame_boxes',
              'recorded_at': datetime.now(timezone.utc).isoformat(), 'decision': decision,
              'source_sha256': item['source_sha256'], 'proposal_sha256': item['proposal_sha256'],
              'label_sha256': digest(label_bytes(boxes)), 'boxes': boxes,
              'complete_frame': approved, 'source_provenance': item['provenance'],
              'prior_identity_decisions': item['identity_decisions'],
              'applied_to_training': False}
    with path.open('a+') as out:
        fcntl.flock(out, fcntl.LOCK_EX)
        out.seek(0)
        previous = None
        for line in out:
            old = json.loads(line)
            if old['item_id'] == item['item_id']:
                previous = old
        if previous and all(previous.get(k) == record[k] for k in
                            ('decision', 'source_sha256', 'proposal_sha256', 'label_sha256', 'complete_frame')):
            return previous
        out.write(json.dumps(record) + '\n'); out.flush(); os.fsync(out.fileno())
    return record


def copy_raw(source, images, roots):
    source = Path(source).resolve()
    # Only known raw collection paths, never autolabel galleries/annotated renders.
    rels = [source.relative_to(root) for root in roots if source.is_relative_to(root)]
    if not any((len(p.parts) == 2 and p.parts[0] in
                ('classified', 'unclassified', 'processed', 'reclassified')) or
               (len(p.parts) == 3 and p.parts[0] == 'beast_cam') for p in rels):
        raise ValueError('Source is not in an allowed raw collection')
    if source.suffix.lower() not in ('.jpg', '.jpeg'):
        raise ValueError('Expected raw JPEG')
    with tempfile.TemporaryDirectory(dir=images.parent) as tmp:
        target = Path(tmp) / 'raw.jpg'
        subprocess.run([sys.executable, '-c',
            'import shutil,sys;shutil.copyfile(sys.argv[1],sys.argv[2])', str(source), str(target)],
            check=True, timeout=8, capture_output=True)
        content = target.read_bytes()
        if not 0 < len(content) <= 10 * 1024 * 1024:
            raise ValueError('Unexpected image size')
        with Image.open(io.BytesIO(content)) as img:
            if img.format != 'JPEG': raise ValueError('Expected JPEG bytes')
            width, height = img.size
            img.verify()
        sha = digest(content)
        dest = images / (sha + '.jpg')
        if dest.exists():
            if digest(dest.read_bytes()) != sha: raise ValueError('Local source hash conflict')
        else:
            with dest.open('xb') as out:
                out.write(content); out.flush(); os.fsync(out.fileno())
        return sha, dest, width, height, str(source)


def teacher_boxes(item, teacher):
    import cv2
    frame = cv2.imread(item['source_paths'][0])
    result = teacher(frame, conf=.20, device='cpu', verbose=False)[0]
    boxes = []
    for prediction in result.boxes:
        cls = int(prediction.cls[0])
        if not 0 <= cls < 80: raise ValueError('Teacher must use the stock COCO vocabulary')
        if teacher.names[cls].replace(' ', '_') != COCO[cls]:
            raise ValueError('Teacher class mapping differs from the Rook contract')
        coords = [int(v) for v in prediction.xyxy[0].tolist()]
        x1, y1, x2, y2 = coords
        crop = frame[max(0,y1):min(item['height'],y2), max(0,x1):min(item['width'],x2)]
        matched = []
        if crop.size and min(crop.shape[:2]) >= 24:
            scale = 384 / max(crop.shape[:2])
            if scale < 1:
                crop = cv2.resize(crop, (max(1,int(crop.shape[1]*scale)), max(1,int(crop.shape[0]*scale))))
            ok, encoded = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if ok:
                matched = [d for d in item['identity_decisions'] if d['proposal'].get('cache_hash') == digest(encoded.tobytes())]
        suggested = {d['corrected_label'] if d['decision'] == 'correct' else d['proposal']['label'] for d in matched}
        if len(suggested) == 1 and next(iter(suggested)) in CUSTOM_CLASSES:
            cls = CLASSES.index(next(iter(suggested)))
        xyxy = [max(0,min(1,x1/item['width'])), max(0,min(1,y1/item['height'])),
                max(0,min(1,x2/item['width'])), max(0,min(1,y2/item['height']))]
        if xyxy[0] < xyxy[2] and xyxy[1] < xyxy[3]:
            boxes.append({'class_id': cls, 'xyxy': xyxy})
    # Integer crop coordinates can collapse overlapping teacher proposals to the
    # same box. Deduplicate machine proposals, never human review history.
    boxes = list({canonical(b): b for b in boxes}.values())
    return normalize_boxes(boxes)


def prepare(catalog, output, roots, teacher=None, teacher_hash=None):
    catalog, output = require_local(catalog), require_local(output)
    roots = [Path(p).expanduser().resolve() for p in roots]
    images = output / 'images'; images.mkdir(parents=True, exist_ok=True)
    latest = {}
    for line in (catalog / 'review_decisions.jsonl').read_text().splitlines():
        record = json.loads(line); latest[record['item_id']] = record
    selected = [r for r in latest.values() if r['decision'] in ('approve', 'correct', 'interesting')]
    items, errors = {}, []
    for record in selected:
        proposal = record['proposal']; failures = []
        for source in proposal.get('source_paths', []):
            try:
                sha, dest, width, height, origin = copy_raw(source, images, roots)
                item = items.setdefault(sha, {'kind': 'box_review', 'item_id': sha,
                    'source_sha256': sha, 'source_paths': [str(dest)], 'width': width, 'height': height,
                    'source_filename': proposal['source_filename'], 'capture_time': proposal.get('capture_time'),
                    'identity_decisions': [], 'provenance': {'raw_collection_path': origin,
                        'copied_at': datetime.now(timezone.utc).isoformat(), 'image_sha256': sha}})
                item['identity_decisions'].append(record)
                break
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                failures.append(type(exc).__name__)
        else:
            errors.append({'item_id': record['item_id'], 'errors': failures or ['no raw source']})
    for index, item in enumerate(items.values(), 1):
        item['boxes'] = teacher_boxes(item, teacher) if teacher else []
        item['teacher_sha256'] = teacher_hash
        # Hash the semantic proposal, not timestamps generated by repeat preparation.
        item['proposal_sha256'] = digest(canonical({'source': item['source_sha256'],
            'boxes': item['boxes'], 'teacher': teacher_hash,
            'identity_decisions': item['identity_decisions']}))
        print(f'Prepared {index}/{len(items)} raw frames', flush=True)
    report = {'frames': len(items), 'selected_decisions': len(selected), 'errors': errors,
              'approved_for_training': 0}
    for filename, value in (('box_batch.json', list(items.values())), ('box_preparation.json', report)):
        tmp = output / (filename + '.tmp'); tmp.write_text(json.dumps(value, indent=2)+'\n'); tmp.replace(output/filename)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=Path.home()/'rook-local/catalog')
    parser.add_argument('--output', type=Path, default=Path.home()/'rook-local/box-review')
    parser.add_argument('--archive', type=Path, default=Path.home()/'Library/CloudStorage/Dropbox/Rook/archive')
    parser.add_argument('--staging', type=Path, default=Path.home()/'rook-staging')
    parser.add_argument('--teacher', type=Path, help='existing stock YOLO checkpoint on local disk; no downloads')
    args = parser.parse_args(); teacher = None; sha = None
    if args.teacher:
        path = require_local(args.teacher)
        if not path.is_file(): parser.error('Teacher checkpoint must already exist locally')
        sha = digest(path.read_bytes())
        from ultralytics import YOLO
        teacher = YOLO(str(path))
    report = prepare(args.catalog, args.output, [args.archive, args.staging], teacher, sha)
    print(json.dumps(report, indent=2))
    if report['errors']: raise SystemExit(1)


if __name__ == '__main__': main()
