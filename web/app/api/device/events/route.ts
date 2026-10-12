import {handleIngest,privateHeaders} from '@/lib/ingest';
import {ingestStore} from '@/lib/supabase';
export const runtime='nodejs';
export const dynamic='force-dynamic';
export async function POST(request:Request) {
  try { return await handleIngest(request,ingestStore()); }
  catch { return Response.json({error:'Ingestion unavailable'},{status:503,headers:privateHeaders}); }
}
