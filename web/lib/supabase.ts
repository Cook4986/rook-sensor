import 'server-only';
import { createServerClient } from '@supabase/ssr';
import { createClient } from '@supabase/supabase-js';
import { cookies } from 'next/headers';
import { Denied } from './protocol';
import type { IngestStore } from './ingest';

export function configured() { return !!(process.env.SUPABASE_URL && process.env.SUPABASE_PUBLISHABLE_KEY); }
export async function sessionClient() {
  const jar = await cookies();
  return createServerClient(process.env.SUPABASE_URL!,process.env.SUPABASE_PUBLISHABLE_KEY!, {
    cookieOptions:{httpOnly:true,secure:process.env.NODE_ENV==='production',sameSite:'lax',path:'/'},
    cookies:{getAll:()=>jar.getAll(),setAll:(values)=>{
      try { values.forEach(({name,value,options})=>jar.set(name,value,options)); }
      catch { /* Server Components are read-only; proxy refreshes cookies. */ }
    }}
  });
}
export async function ownerSession() {
  if (!configured()) throw new Denied(503,'Private feed is not connected yet');
  const client = await sessionClient();
  const {data:{user},error} = await client.auth.getUser();
  if (error || !user) throw new Denied(401,'Sign in required');
  const {data:owner,error:ownerError} = await client.from('rook_owners').select('user_id').eq('user_id',user.id).maybeSingle();
  if (ownerError) throw new Denied(503,'Private feed unavailable');
  if (!owner) throw new Denied(403,'Owner access required');
  return client;
}
export function ingestStore():IngestStore {
  const client = createClient(process.env.SUPABASE_URL!, process.env.SUPABASE_SECRET_KEY!,
    {auth:{persistSession:false,autoRefreshToken:false}});
  return {
    async authenticate(hash) {
      const {data,error}=await client.rpc('rook_authenticate_device',{p_token_hash:hash});
      if (error) throw error;
      return data;
    },
    async commit(hash, observation) {
      const {data,error}=await client.rpc('rook_ingest',{p_token_hash:hash,p_event_json:observation.eventJson,
        p_image:observation.image ? '\\x'+observation.image.toString('hex') : null});
      if(error) {
        const status:Record<string,number>={P0401:401,P0403:403,P0409:409,P0422:422,P0429:429};
        if(status[error.code]) throw new Denied(status[error.code], 'Observation rejected');
        throw error;
      }
      return data;
    }
  };
}
