import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,writeFile,mkdtemp,stat,rm} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {resolve} from 'node:path';
import {Client} from 'pg';
import {fixture,owner,outsider,token} from './fixtures';
import {digest} from '../lib/protocol';
const socket=process.env.ROOK_PG_TEST_DIR;
test('Postgres: atomic images, restart-safe replay, revocation, RLS and denied RPC access',{skip:!socket},async()=>{
  assert.match(socket!,/^\/private\/tmp\/rook-pg-[A-Za-z0-9_-]+$/);
  const config={host:socket,user:process.env.USER,database:'rook_test'};
  const db=new Client(config);await db.connect();
  try {
    await db.query(`do $$begin create role anon;exception when duplicate_object then null;end$$;
      do $$begin create role authenticated;exception when duplicate_object then null;end$$;
      do $$begin create role service_role bypassrls;exception when duplicate_object then null;end$$;
      create schema auth;create table auth.users(id uuid primary key);
      create function auth.uid() returns uuid language sql stable as $$select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid$$;
      grant usage on schema auth to authenticated;grant execute on function auth.uid() to authenticated;`);
    await db.query(await readFile(process.env.ROOK_MIGRATION_PATH||resolve('../supabase/migrations/202610120001_private_feed.sql'),'utf8'));
    await db.query('insert into auth.users values($1),($2)',[owner,outsider]);
    await db.query('insert into rook_owners(user_id) values($1)',[owner]);
    await db.query('insert into rook_devices(device_id,owner_id) values($1,$2)',['yard-pi',owner]);
    await db.query('insert into rook_private.device_tokens(token_hash,device_id) values($1,$2)',[digest(token),'yard-pi']);
    const f=await fixture(); const names=Array.from({length:80},(_,i)=>i===7?'truck':`class_${i}`);
    await db.query('insert into rook_private.models values($1,$2,$3,$4)',['yard-pi',f.event.model.artifact_sha256,f.event.model.classes_sha256,JSON.stringify({model:f.event.model,class_names:names})]);
    const commit=(client:Client,json=f.eventJson,image:Buffer|null=f.image)=>client.query('select rook_ingest($1,$2,$3) as receipt',[digest(token),json,image]);
    await db.query('set role service_role');
    const second=new Client(config);await second.connect();await second.query('set role service_role');
    const [firstResult,secondResult]=await Promise.all([commit(db),commit(second)]);
    const receipt=firstResult.rows[0].receipt;
    assert.equal(receipt.event_sha256,f.eventHash);
    assert.deepEqual(secondResult.rows[0].receipt,receipt);await second.end();
    const reopened=new Client(config);await reopened.connect();await reopened.query('set role service_role');
    assert.deepEqual((await commit(reopened)).rows[0].receipt,receipt);await reopened.end();
    const changed=structuredClone(f.event);changed.detections[0].confidence=.5;
    await assert.rejects(commit(db,JSON.stringify(changed)),{code:'P0409'});
    const unseen=await fixture();unseen.event.model.artifact_sha256='c'.repeat(64);
    await assert.rejects(commit(db,JSON.stringify(unseen.event),unseen.image),{code:'P0422'});
    const bad=await fixture();bad.event.detections[0].label='invented';
    await assert.rejects(commit(db,JSON.stringify(bad.event),bad.image),{code:'P0422'});
    await db.query('reset role');
    assert.equal((await db.query('select count(*)::int as n from rook_events')).rows[0].n,1);
    assert.equal((await db.query('select count(*)::int as n from rook_images')).rows[0].n,1);
    for(const uid of [outsider,owner]) {
      await db.query('set role authenticated');await db.query("select set_config('request.jwt.claim.sub',$1,false)",[uid]);
      for(const table of ['rook_events','rook_images','rook_devices','rook_owners'])
        assert.equal((await db.query(`select count(*)::int as n from ${table}`)).rows[0].n,uid===owner?1:0);
      await assert.rejects(commit(db),{code:'42501'});
      await assert.rejects(db.query('delete from rook_events'),{code:'42501'});
      await assert.rejects(db.query('select * from rook_private.device_tokens'),{code:'42501'});
      await db.query('reset role');
    }
    await db.query('set role anon');
    await assert.rejects(db.query('select * from rook_images'),{code:'42501'});
    await assert.rejects(commit(db),{code:'42501'});await db.query('reset role');
    // Simulate a late image-write failure: no event/receipt or quota increment survives.
    await db.query(`create function reject_image() returns trigger language plpgsql as $$begin raise exception 'injected failure';end$$;
      create trigger reject_image before insert on rook_images for each row execute function reject_image();`);
    const next=await fixture();await assert.rejects(commit(db,next.eventJson,next.image));
    assert.equal((await db.query('select event_count from rook_devices')).rows[0].event_count,1);
    assert.equal((await db.query('select count(*)::int as n from rook_events')).rows[0].n,1);
    await db.query('drop trigger reject_image on rook_images');
    await db.query('update rook_devices set event_count=1000');
    await assert.rejects(commit(db,next.eventJson,next.image),{code:'P0429'});
    await db.query('update rook_devices set event_count=1');
    await db.query('update rook_private.device_tokens set revoked_at=now()');
    await db.query('set role service_role');
    assert.equal((await db.query('select rook_authenticate_device($1) as device',[digest(token)])).rows[0].device,null);
    await assert.rejects(commit(db),{code:'P0401'});
    await db.query('reset role');
    // Exercise the actual operator entrypoint, including exclusive secret output.
    const directory=await mkdtemp('/private/tmp/rook-provision-');
    try {
      const model={name:'test-model',format:'pytorch',artifact_sha256:digest('weights'),classes_sha256:digest(JSON.stringify(names))};
      const manifest={model,class_names:names,artifact_files:[{path:'test.pt',bytes:7,sha256:model.artifact_sha256}]};
      const modelPath=directory+'/manifest.json',tokenPath=directory+'/token';
      await writeFile(modelPath,JSON.stringify(manifest));
      const run=(path=tokenPath)=>execFileSync(process.execPath,[resolve('node_modules/tsx/dist/cli.mjs'),resolve('scripts/provision.ts'),
        '--owner',owner,'--device','provision-test','--model',modelPath,'--token-file',path],
        {env:{...process.env,DATABASE_URL:`postgresql://${encodeURIComponent(process.env.USER!) }@/rook_test?host=${encodeURIComponent(socket!)}`},stdio:'pipe'});
      assert.match(run().toString(),/Provisioned device/);
      assert.equal((await stat(tokenPath)).mode&0o777,0o600);
      const newHash=digest((await readFile(tokenPath,'utf8')).trim());
      assert.equal((await db.query('select rook_authenticate_device($1) as device',[newHash])).rows[0].device,'provision-test');
      assert.throws(()=>run()); // Existing token file is never overwritten.
      assert.equal((await db.query("select count(*)::int as n from rook_private.device_tokens where device_id='provision-test'")).rows[0].n,1);
      manifest.model.name='different-model';await writeFile(modelPath,JSON.stringify(manifest));
      assert.throws(()=>run(directory+'/conflicting-token'));
      await assert.rejects(stat(directory+'/conflicting-token'),{code:'ENOENT'});
    } finally {await rm(directory,{recursive:true,force:true});}
  } finally {await db.end();}
});
