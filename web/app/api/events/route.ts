import {ownerSession} from '@/lib/supabase';
import {readFeed} from '@/lib/owner-routes';
export const dynamic='force-dynamic';
export async function GET() {
  return readFeed(ownerSession);
}
