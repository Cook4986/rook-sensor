import test from 'node:test';
import assert from 'node:assert/strict';
import {Denied,digest,parseEnvelope,validateEvent} from '../lib/protocol';
import {handleIngest,type IngestStore} from '../lib/ingest';
import {readFeed,readImage} from '../lib/owner-routes';
import {fixture,token} from './fixtures';
import {execFileSync} from 'node:child_process';
import {resolve} from 'node:path';

test('actual Python worker envelope is accepted without reserializing its float bytes',async()=>{
  const python=process.env.ROOK_TEST_PYTHON;
  if(!python) throw new Error('ROOK_TEST_PYTHON must point to the project Python runtime');
  const output=execFileSync(python,['-c',`import sys,json
sys.path.insert(0,sys.argv[1])
from rook_events import make_event,canonical,sha256
e=make_event('yard-pi','2026-10-12T18:30:00.123456Z',{'name':'fixture','format':'ncnn','artifact_sha256':'a'*64,'classes_sha256':'b'*64},[{'class_id':7,'label':'truck','confidence':1.0,'xyxy':[0.0,0.1,1.0,0.9],'subclass':None}])
print(json.dumps({'event_json':canonical(e).decode(),'image_base64':None,'expected':sha256(canonical(e))}))`,process.env.ROOK_APP_PATH||resolve('../app')]);
  const {expected,...envelope}=JSON.parse(output.toString());
  const observation=await parseEnvelope(envelope);
  assert.equal(observation.eventHash,expected);
  assert.match(observation.eventJson,/1\.0/);
});
test('invalid images, geometry, schema and spoofed times are rejected',async()=>{
  const f=await fixture();
  for(const edit of [(e:any)=>e.received_at='2026-10-12T18:00:00Z',(e:any)=>e.detections[0].class_id=107,
    (e:any)=>e.detections[0].xyxy=[1,0,0,1],(e:any)=>e.schema_version=2,
    (e:any)=>e.captured_at='2026-02-30T18:00:00Z']) {
    const e=structuredClone(f.event);edit(e);assert.throws(()=>validateEvent(e),Denied);
  }
  await assert.rejects(parseEnvelope({event_json:f.eventJson,image_base64:Buffer.from('not a JPEG').toString('base64')}),Denied);
  const e=structuredClone(f.event);e.image.width=99;
  await assert.rejects(parseEnvelope({event_json:JSON.stringify(e),image_base64:f.image.toString('base64')}),Denied);
  const bad=Buffer.from('not a JPEG');e.image.byte_length=bad.length;e.image.sha256=digest(bad);
  await assert.rejects(parseEnvelope({event_json:JSON.stringify(e),image_base64:bad.toString('base64')}),Denied);
});
test('guessed image identity does not return data',async()=>{
  const authorize=async()=>({from:()=>({select:()=>({eq:()=>({eq:()=>({maybeSingle:async()=>({data:null,error:null})})})})})}) as any;
  assert.equal((await readImage(authorize,'yard-pi','00000000-0000-4000-8000-000000000001')).status,404);
});
test('ingestion denies absent, revoked and cross-device credentials before commit',async()=>{
  const f=await fixture();let commits=0;
  const store:IngestStore={authenticate:async()=>null,commit:async()=>{commits++;throw new Error('must not commit');}};
  const request=(credential=token)=>new Request('https://rook.test/api/device/events',{method:'POST',headers:{authorization:'Bearer '+credential,'content-type':'application/json','idempotency-key':'yard-pi/'+f.event.event_id},body:JSON.stringify({event_json:f.eventJson,image_base64:f.image.toString('base64')})});
  assert.equal((await handleIngest(request(''),store)).status,401);
  assert.equal((await handleIngest(request(),store)).status,401);
  store.authenticate=async()=> 'another-device';
  assert.equal((await handleIngest(request(),store)).status,403);
  assert.equal(commits,0);
  store.authenticate=async()=> 'yard-pi';
  store.commit=async()=>{throw new Denied(401,'revoked during validation');};
  assert.equal((await handleIngest(request(),store)).status,401);
});
test('owner routes reject signed-out and non-owner sessions without data access',async()=>{
  for(const status of [401,403]) {
    const authorize=async()=>{throw new Denied(status,'denied');};
    const feed=await readFeed(authorize);const image=await readImage(authorize,'yard-pi','00000000-0000-4000-8000-000000000001');
    assert.equal(feed.status,status);assert.equal(image.status,status);
    assert.match(feed.headers.get('cache-control')!,/no-store/);
  }
});
test('oversized and invalid idempotency requests never commit',async()=>{
  const f=await fixture();const store:IngestStore={authenticate:async()=> 'yard-pi',commit:async()=>{throw new Error('unexpected');}};
  const headers={authorization:'Bearer '+token,'content-type':'application/json'};
  const oversized=new Request('https://rook.test',{method:'POST',headers:{...headers,'content-length':'9999999'},body:'{}'});
  assert.equal((await handleIngest(oversized,store)).status,413);
  const wrong=new Request('https://rook.test',{method:'POST',headers,body:JSON.stringify({event_json:f.eventJson,image_base64:f.image.toString('base64')})});
  assert.equal((await handleIngest(wrong,store)).status,422);
});
