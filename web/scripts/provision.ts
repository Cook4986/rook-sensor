// Explicit operator CLI; never imported by a web route. Does not create Auth users.
import {parseArgs} from 'node:util';
import {readFile,writeFile,unlink} from 'node:fs/promises';
import {resolve} from 'node:path';
import {randomBytes} from 'node:crypto';
import {isDeepStrictEqual} from 'node:util';
import {Client} from 'pg';
import {digest,devicePattern,eventPattern} from '../lib/protocol';
import {verifyManifest} from '../lib/model-manifest';
const {values}=parseArgs({options:{owner:{type:'string'},device:{type:'string'},model:{type:'string'},'token-file':{type:'string'}}});
if(!values.owner||!eventPattern.test(values.owner)||!values.device||!devicePattern.test(values.device)||!values.model||!values['token-file']||!process.env.DATABASE_URL)
  throw new Error('Require DATABASE_URL and --owner UUID --device ID --model FILE --token-file NEW_PRIVATE_FILE');
const tokenPath=resolve(values['token-file']);
if(tokenPath.includes('/Dropbox/')||tokenPath.includes('/CloudStorage/')||tokenPath.startsWith(process.cwd()+'/')) throw new Error('Store device token outside source and synced folders');
const manifest=verifyManifest(JSON.parse(await readFile(values.model,'utf8')));
const token='rook_device_'+randomBytes(32).toString('base64url');
const client=new Client({connectionString:process.env.DATABASE_URL});
let created=false,commitStarted=false;
try {
  await client.connect();await client.query('begin');
  await client.query('insert into public.rook_owners(user_id) values($1) on conflict(user_id) do nothing',[values.owner]);
  await client.query('insert into public.rook_devices(device_id,owner_id) values($1,$2) on conflict(device_id) do nothing',[values.device,values.owner]);
  const existing=await client.query('select owner_id from public.rook_devices where device_id=$1',[values.device]);
  if(existing.rows[0].owner_id!==values.owner) throw new Error('Device belongs to another owner');
  await client.query('insert into rook_private.models values($1,$2,$3,$4) on conflict do nothing',
    [values.device,manifest.model.artifact_sha256,manifest.model.classes_sha256,JSON.stringify(manifest)]);
  const registered=await client.query('select manifest from rook_private.models where device_id=$1 and artifact_sha256=$2 and classes_sha256=$3',
    [values.device,manifest.model.artifact_sha256,manifest.model.classes_sha256]);
  if(!isDeepStrictEqual(registered.rows[0].manifest,manifest)) throw new Error('Registered manifest differs');
  await client.query('insert into rook_private.device_tokens(token_hash,device_id) values($1,$2)',[digest(token),values.device]);
  await writeFile(tokenPath,token+'\n',{flag:'wx',mode:0o600});created=true;
  commitStarted=true;await client.query('commit');
  console.log('Provisioned device. Token saved to private file; never printed.');
} catch(error) {
  await client.query('rollback').catch(()=>{});
  if(created && !commitStarted) await unlink(tokenPath);
  console.error(commitStarted
    ? 'Commit outcome unconfirmed. Private token file retained; verify database registration before use or retry.'
    : 'Provisioning failed; transaction rolled back.');process.exitCode=1;
} finally { await client.end(); }
