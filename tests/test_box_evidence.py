import copy
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app'))
from box_review import prepare, append_box_decision, CLASSES, digest, normalize_boxes
from approved_dataset import export_snapshot, verify_snapshot
from archive_review import make_server
import test_archive_review as review_tests


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.archive=self.root/'archive'; self.catalog=self.root/'catalog'
        self.package=self.root/'package'; self.catalog.mkdir(); (self.archive/'classified').mkdir(parents=True)
        records=[]
        for i,color in enumerate(('red','blue')):
            name=f'classified_2026101{i+1}_070000.jpg'; path=self.archive/'classified'/name
            Image.new('RGB',(64,40),color).save(path)
            records.append({'item_id':str(i),'decision':'approve','corrected_label':None,
                'scope':'candidate_identity','proposal':{'source_paths':[str(path)],'source_filename':name,
                    'label':'trash_truck','cache_hash':str(i),'capture_time':f'2026-10-1{i+1}T07:00:00'}})
        (self.catalog/'review_decisions.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))
        self.report=prepare(self.catalog,self.package,[self.archive]); self.items=json.loads((self.package/'box_batch.json').read_text())
        self.ledger=self.package/'box_decisions.jsonl'

    def data(self,item,**changes):
        data={'source_sha256':item['source_sha256'],'proposal_sha256':item['proposal_sha256'],
              'decision':'approve_boxes','complete_frame':True,
              'boxes':[{'class_id':80,'xyxy':[.1,.1,.4,.5]}, {'class_id':0,'xyxy':[.6,.1,.8,.7]}]}
        return {**data,**changes}

    def approved(self):
        for item in self.items: append_box_decision(self.ledger,item,self.data(item))
        return {'groups_reviewed':True,'enabled_classes':['trash_truck'],
                'items':{item['item_id']:{'split':'train' if i==0 else 'val','visit_group':f'visit-{i}'} for i,item in enumerate(self.items)}}

    def snapshot(self):
        return export_snapshot(self.package,self.root/'snapshot',self.approved())

    def test_raw_copy_and_rerun_preserve_hashes_and_human_ledger(self):
        self.assertEqual(self.report['frames'],2)
        self.assertFalse(self.ledger.exists())
        self.approved(); before=self.ledger.read_bytes(); proposals=[i['proposal_sha256'] for i in self.items]
        prepare(self.catalog,self.package,[self.archive])
        self.assertEqual(proposals,[i['proposal_sha256'] for i in json.loads((self.package/'box_batch.json').read_text())])
        self.assertEqual(self.ledger.read_bytes(),before)
        for item in self.items:
            self.assertEqual(digest(Path(item['source_paths'][0]).read_bytes()),item['source_sha256'])

    def test_identity_alone_cannot_export(self):
        plan={'groups_reviewed':True,'enabled_classes':['trash_truck'],'items':{}}
        with self.assertRaises(OSError): export_snapshot(self.package,self.root/'snapshot',plan)

    def test_approval_requires_complete_frame_exact_source_and_proposal(self):
        item=self.items[0]
        for change in ({'complete_frame':False},{'source_sha256':'stale'}, {'proposal_sha256':'stale'}, {'boxes':[]}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                append_box_decision(self.ledger,item,self.data(item,**change))
        self.assertFalse(self.ledger.exists())
        Path(item['source_paths'][0]).write_bytes(b'tampered')
        with self.assertRaises(ValueError): append_box_decision(self.ledger,item,self.data(item))

    def test_defer_is_not_approval_and_empty_is_explicit(self):
        item=self.items[0]
        rec=append_box_decision(self.ledger,item,self.data(item,decision='defer'))
        self.assertFalse(rec['complete_frame'])
        rec=append_box_decision(self.ledger,item,self.data(item,decision='empty_frame',boxes=[]))
        self.assertEqual(rec['label_sha256'],digest(b''))
        append_box_decision(self.ledger,item,self.data(item,decision='empty_frame',boxes=[]))
        self.assertEqual(len(self.ledger.read_text().splitlines()),2)

    def test_invalid_geometry_and_reserved_class_are_rejected(self):
        for box in ({'class_id':107,'xyxy':[.1,.1,.5,.5]}, {'class_id':80,'xyxy':[0,0,float('nan'),1]},
                    {'class_id':80,'xyxy':[.8,.1,.2,.5]}, {'class_id':80,'xyxy':[-.1,0,1,1]}):
            with self.assertRaises(ValueError):normalize_boxes([box])
        self.assertEqual(len(CLASSES),110)

    def test_annotated_source_is_not_allowed(self):
        records=[json.loads(x) for x in (self.catalog/'review_decisions.jsonl').read_text().splitlines()]
        path=self.archive/'autolabel/review/fake.jpg';path.parent.mkdir(parents=True)
        Image.new('RGB',(64,40)).save(path)
        records[0]['proposal']['source_paths']=[str(path)]
        (self.catalog/'review_decisions.jsonl').write_text(json.dumps(records[0])+'\n')
        report=prepare(self.catalog,self.root/'bad-package',[self.archive])
        self.assertEqual(report['frames'],0);self.assertEqual(len(report['errors']),1)

    def test_snapshot_is_approved_and_cannot_be_overwritten(self):
        dataset=self.snapshot(); info=verify_snapshot(dataset,min_train=1,min_val=1)
        self.assertEqual(info['coverage']['val']['trash_truck'],1)
        self.assertIn('person',info['base_classes_in_val'])
        with self.assertRaisesRegex(ValueError,'Insufficient'):verify_snapshot(dataset)
        with self.assertRaises(ValueError):export_snapshot(self.package,dataset.parent,self.approved())

    def test_changed_labels_or_unmanifested_images_fail(self):
        dataset=self.snapshot(); root=dataset.parent
        label=next((root/'labels/train').glob('*.txt'));original=label.read_bytes();label.write_bytes(b'')
        with self.assertRaisesRegex(ValueError,'Label hash'):verify_snapshot(dataset,1,1)
        label.write_bytes(original)
        (root/'images/train/extra.jpg').write_bytes(b'extra')
        with self.assertRaisesRegex(ValueError,'Unapproved'):verify_snapshot(dataset,1,1)

    def test_visit_overlap_rejected_before_export(self):
        plan=self.approved()
        for value in plan['items'].values():value['visit_group']='same-visit'
        with self.assertRaisesRegex(ValueError,'crosses'):export_snapshot(self.package,self.root/'snapshot',plan)

    def test_runtime_label_cache_is_discarded_before_model_loading(self):
        from approved_dataset import discard_label_caches
        dataset=self.snapshot(); cache=dataset.parent/'labels/train.cache';cache.write_bytes(b'untrusted cached labels')
        verify_snapshot(dataset,1,1)
        discard_label_caches(dataset)
        self.assertFalse(cache.exists())
        self.assertTrue(next((dataset.parent/'labels/train').glob('*.txt')).exists())

    def test_trainer_refuses_legacy_dataset_before_model_import(self):
        import train_custom_model as trainer
        data=self.root/'legacy.yaml';data.write_text('path: /tmp\ntrain: images/train\nval: images/val\n')
        with patch.object(sys,'argv',['trainer','--data',str(data),'--verify-only']),patch.dict(sys.modules,{'ultralytics':None}):
            with self.assertRaisesRegex(SystemExit,'preflight refused'):trainer.main()

    def test_baseline_bound_to_checkpoint_snapshot_and_resolution(self):
        from train_custom_model import validate_baseline
        dataset=self.snapshot(); evidence=verify_snapshot(dataset,1,1); base=self.root/'base.pt';base.write_bytes(b'stock')
        p=self.root/'baseline.json'; data={'imgsz':1088,'snapshot_sha256':evidence['manifest_sha256'],
            'base_sha256':digest(base.read_bytes()),'class_map50':{'person':.5}}
        p.write_text(json.dumps(data)); self.assertEqual(validate_baseline(p,evidence,base)['person'],.5)
        for change in ({'imgsz':640},{'snapshot_sha256':'other'},{'base_sha256':'other'},{'class_map50':{}}):
            p.write_text(json.dumps({**data,**change}))
            with self.assertRaises(ValueError):validate_baseline(p,evidence,base)


class BoxHTTPTest(unittest.TestCase):
    request=review_tests.ReviewTest.request
    login=review_tests.ReviewTest.login
    tearDown=review_tests.ReviewTest.tearDown

    def setUp(self):
        self.fixture=EvidenceTest();self.fixture.setUp();self.temp=self.fixture.temp;self.root=self.fixture.root
        self.server,self.url=make_server(self.fixture.package,self.root,self.root,box_mode=True)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port);self.cookie=''

    def test_box_routes_require_auth_and_explicit_review(self):
        item=self.fixture.items[0]
        response,_=self.request('GET','/api/items');self.assertEqual(response.status,403)
        self.login();response,_=self.request('GET','/image?item='+item['item_id']);self.assertEqual(response.status,200)
        payload=self.fixture.data(item,complete_frame=False);payload['item_id']=item['item_id']
        headers={'Content-Type':'application/json','Origin':f'http://127.0.0.1:{self.server.server_port}'}
        response,_=self.request('POST','/api/decisions',json.dumps(payload),**headers);self.assertEqual(response.status,400)
        payload['complete_frame']=True
        response,_=self.request('POST','/api/decisions',json.dumps(payload),**headers);self.assertEqual(response.status,200)
        Path(item['source_paths'][0]).write_bytes(b'tampered')
        response,_=self.request('GET','/image?item='+item['item_id']);self.assertEqual(response.status,409)
        response,_=self.request('POST','/api/decisions',json.dumps(payload),**headers);self.assertEqual(response.status,400)


if __name__=='__main__':unittest.main()
