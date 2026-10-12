import {redirect} from 'next/navigation';
import {ownerSession,configured} from '@/lib/supabase';
import {Denied} from '@/lib/protocol';
import FeedView,{Shell} from './feed-view';
import Link from 'next/link';
export const dynamic='force-dynamic';
export default async function Home() {
  if(!configured()) return <Shell><section className="intro"><p className="eyebrow">YOUR PRIVATE FEED</p><h1>Almost home<span>.</span></h1><p>Your private connection is being prepared. Connect your owner account to see observations here.</p><Link className="button" href="/preview">Explore the design preview →</Link></section></Shell>;
  try {
    const client=await ownerSession();
    const {data,error}=await client.from('rook_events').select('device_id,event_id,captured_at,received_at,observation').order('received_at',{ascending:false}).limit(50);
    if(error) throw error;
    return <FeedView events={data||[]} lastReceived={data?.[0]?.received_at}/>;
  } catch(error) {
    if(error instanceof Denied && error.status===401) redirect('/login');
    return <Shell><section className="intro"><h1>Private by design<span>.</span></h1><p>{error instanceof Denied?error.message:'The feed is temporarily unavailable.'}</p><Link href="/login">Return to sign in →</Link></section></Shell>;
  }
}
