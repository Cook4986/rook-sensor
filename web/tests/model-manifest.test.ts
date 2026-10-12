import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {verifyManifest} from '../lib/model-manifest';
test('registration verifies Python NCNN and vocabulary fingerprints',()=>{
  const python=process.env.ROOK_TEST_PYTHON;
  if(!python) throw new Error('ROOK_TEST_PYTHON is required');
  const raw=execFileSync(python,['-c',`import hashlib,json
def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def sha(x):return hashlib.sha256(x).hexdigest()
names=['class_'+str(i) for i in range(80)]
files=[{'path':'model.bin','bytes':1,'sha256':sha(b'a')},{'path':'model.param','bytes':1,'sha256':sha(b'b')}]
print(json.dumps({'model':{'name':'model','format':'ncnn','artifact_sha256':sha(canonical(files)),'classes_sha256':sha(canonical(names))},'class_names':names,'artifact_files':files}))`]);
  const manifest=JSON.parse(raw.toString());assert.equal(verifyManifest(manifest),manifest);
  const changed=structuredClone(manifest);changed.class_names[7]='invented';assert.throws(()=>verifyManifest(changed));
  const changedFile=structuredClone(manifest);changedFile.artifact_files[0].bytes=2;assert.throws(()=>verifyManifest(changedFile));
});
