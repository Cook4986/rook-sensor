import {Denied} from './protocol';
import {errorResponse,validIdentity} from './feed';
import {privateHeaders} from './ingest';
import type {SupabaseClient} from '@supabase/supabase-js';
type Authorize=()=>Promise<SupabaseClient>;
export async function readFeed(authorize:Authorize) {
  try {
    const client=await authorize();
    const {data,error}=await client.from('rook_events').select('device_id,event_id,captured_at,received_at,observation').order('received_at',{ascending:false}).limit(50);
    if(error) throw error;
    return Response.json({events:data},{headers:privateHeaders});
  } catch(error) { return errorResponse(error); }
}
export async function readImage(authorize:Authorize,device:string,event:string) {
  try {
    const client=await authorize(); validIdentity(device,event);
    const {data,error}=await client.from('rook_images').select('content').eq('device_id',device).eq('event_id',event).maybeSingle();
    if(error) throw error;
    if(!data) throw new Denied(404,'Image unavailable');
    if(typeof data.content!=='string' || !/^\\x(?:[a-f0-9]{2})+$/i.test(data.content)) throw new Error('Invalid stored image');
    const image=Buffer.from(data.content.slice(2),'hex');
    return new Response(image,{headers:{...privateHeaders,'Content-Type':'image/jpeg','Content-Disposition':'inline'}});
  } catch(error) { return errorResponse(error); }
}
