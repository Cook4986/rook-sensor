import {ownerSession} from '@/lib/supabase';
import {readImage} from '@/lib/owner-routes';
export const dynamic='force-dynamic';
export async function GET(_request:Request,{params}:{params:Promise<{device:string;event:string}>}) {
  const {device,event}=await params;
  return readImage(ownerSession,device,event);
}
