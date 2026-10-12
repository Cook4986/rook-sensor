"""Export and verify local, human-approved dataset snapshots.

A split plan is explicit input: independent visit groups must be reviewed before
export. Dates/time buckets alone are not proof of independent sightings.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from archive_catalog import require_local
from box_review import CLASSES, CUSTOM_CLASSES, canonical, digest, label_bytes, verified_image


def check_receipt(item, receipt):
    if receipt.get('scope') != 'frame_boxes' or receipt.get('decision') not in ('approve_boxes', 'empty_frame'):
        raise ValueError('Explicit human frame/box approval required')
    if receipt.get('complete_frame') is not True:
        raise ValueError('Incomplete frame review')
    for key in ('source_sha256', 'proposal_sha256'):
        if receipt.get(key) != item.get(key): raise ValueError('Stale source or proposal approval')
    labels = label_bytes(receipt['boxes'])
    if digest(labels) != receipt.get('label_sha256'): raise ValueError('Approved labels changed')
    if bool(labels) != (receipt['decision'] == 'approve_boxes'):
        raise ValueError('Empty/nonempty approval mismatch')
    return labels


def export_snapshot(package, destination, plan):
    package, destination = require_local(package), require_local(destination)
    if destination.exists(): raise ValueError('Snapshot destination must be new; never overwrite')
    if plan.get('groups_reviewed') is not True:
        raise ValueError('An explicitly reviewed independent-visit split plan is required')
    enabled = plan['enabled_classes']
    if not enabled or len(set(enabled)) != len(enabled) or any(c not in CUSTOM_CLASSES or c == 'flood' for c in enabled):
        raise ValueError('Invalid enabled custom classes')
    items = {i['item_id']: i for i in json.loads((package/'box_batch.json').read_text())}
    latest = {}
    for line in (package/'box_decisions.jsonl').read_text().splitlines():
        record = json.loads(line); latest[record['item_id']] = record
    prepared, groups = [], {}
    for sha, assignment in plan['items'].items():
        split, group = assignment['split'], assignment['visit_group']
        if split not in ('train', 'val') or not isinstance(group, str) or not group.strip():
            raise ValueError('Every item needs a train/val split and independent visit group')
        if group in groups and groups[group] != split: raise ValueError('Visit group crosses train/val')
        groups[group] = split
        item, receipt = items[sha], latest[sha]
        labels = check_receipt(item, receipt); content = verified_image(item)
        prepared.append((sha, split, group, content, labels, item, receipt))
    if not prepared: raise ValueError('No reviewed frames selected')
    destination.mkdir(parents=True)
    records = []
    for sha, split, group, content, labels, item, receipt in prepared:
        image = destination/'images'/split/(sha+'.jpg'); image.parent.mkdir(parents=True, exist_ok=True)
        label = destination/'labels'/split/(sha+'.txt'); label.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(content); label.write_bytes(labels)
        records.append({'image': str(image.relative_to(destination)), 'label': str(label.relative_to(destination)),
                        'split': split, 'visit_group': group, 'item': item, 'approval': receipt})
    manifest = {'schema_version': 1, 'groups_reviewed': True, 'enabled_classes': enabled,
                'records': records}
    (destination/'approved_snapshot.json').write_text(json.dumps(manifest, indent=2)+'\n')
    # JSON is also valid YAML and keeps this export independent of YAML dependencies.
    (destination/'dataset.yaml').write_text(json.dumps({'path': str(destination),
        'train': 'images/train', 'val': 'images/val', 'names': CLASSES}, indent=2)+'\n')
    return destination/'dataset.yaml'


def verify_snapshot(dataset, min_train=35, min_val=15):
    import yaml
    dataset = require_local(dataset)
    root = dataset.parent
    config = yaml.safe_load(dataset.read_text())
    if config.get('path') != str(root) or config.get('train') != 'images/train' or config.get('val') != 'images/val':
        raise ValueError('Dataset paths must refer only to this local approved snapshot')
    if config.get('names') != CLASSES: raise ValueError('Class vocabulary does not match the append-only contract')
    if set(config) != {'path', 'train', 'val', 'names'}:
        raise ValueError('Unexpected dataset options; snapshot is not self-contained')
    manifest_bytes = (root/'approved_snapshot.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get('schema_version') != 1 or manifest.get('groups_reviewed') is not True:
        raise ValueError('Reviewed visit groups required')
    enabled = manifest['enabled_classes']
    if not enabled or len(set(enabled)) != len(enabled) or any(c not in CUSTOM_CLASSES or c == 'flood' for c in enabled):
        raise ValueError('Invalid enabled classes')
    seen_images, seen_labels, groups, image_splits = set(), set(), {}, {}
    coverage = {'train': {c:set() for c in enabled}, 'val': {c:set() for c in enabled}}
    base_in_val = set()
    for record in manifest['records']:
        split, group = record['split'], record['visit_group']
        if split not in coverage or not isinstance(group, str) or not group.strip(): raise ValueError('Invalid visit/split')
        if group in groups and groups[group] != split: raise ValueError('Visit group leaks across splits')
        groups[group] = split
        item, receipt = record['item'], record['approval']; labels = check_receipt(item, receipt)
        sha = item['source_sha256']
        for key, folder, suffix, seen in (('image','images','.jpg',seen_images),('label','labels','.txt',seen_labels)):
            expected = f'{folder}/{split}/{sha}{suffix}'
            if record[key] != expected or expected in seen: raise ValueError('Duplicate or unexpected snapshot path')
            target = root/expected
            if target.is_symlink() or not target.resolve().is_relative_to(root): raise ValueError('Snapshot paths must stay local')
            seen.add(expected)
        if digest((root/record['image']).read_bytes()) != sha: raise ValueError('Image hash changed')
        if (root/record['label']).read_bytes() != labels: raise ValueError('Label hash changed')
        if sha in image_splits and image_splits[sha] != split: raise ValueError('Image leaks across splits')
        image_splits[sha] = split
        for box in receipt['boxes']:
            cls = CLASSES[box['class_id']]
            if cls in enabled: coverage[split][cls].add(group)
            if split == 'val' and box['class_id'] < 80: base_in_val.add(cls)
    # Reject unmanifested files which the training dataloader would otherwise consume.
    for folder, seen in (('images',seen_images),('labels',seen_labels)):
        actual = {str(p.relative_to(root)) for p in (root/folder).rglob('*') if p.is_file()}
        if folder == 'labels':
            actual -= {'labels/train.cache', 'labels/val.cache'}
        if actual != seen: raise ValueError('Unapproved or missing files in snapshot')
    failures = []
    for split, minimum in (('train', min_train), ('val', min_val)):
        for cls, visits in coverage[split].items():
            if len(visits) < minimum: failures.append(f'{cls}: {len(visits)} {split} visits < {minimum}')
    if failures: raise ValueError('Insufficient independent coverage: '+ '; '.join(failures))
    if not base_in_val: raise ValueError('Generic-class validation coverage is required')
    return {'manifest_sha256': digest(manifest_bytes), 'enabled_classes': enabled,
            'base_classes_in_val': sorted(base_in_val), 'frames':len(manifest['records']),
            'coverage': {s:{c:len(v) for c,v in counts.items()} for s,counts in coverage.items()}}


def discard_label_caches(dataset):
    # Ultralytics caches parsed labels. Never trust an earlier cache in place of
    # the exact label bytes just verified. These two generated files are disposable.
    root = require_local(dataset).parent
    for split in ('train', 'val'):
        (root/'labels'/f'{split}.cache').unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package',type=Path,required=True)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    dataset=export_snapshot(args.package,args.output,json.loads(args.plan.read_text()))
    print(dataset)
    print(json.dumps(verify_snapshot(dataset),indent=2))


if __name__=='__main__':main()
