import Link from 'next/link';
import type {FeedEvent} from '@/lib/feed';
import {signOut} from './login/actions';
const emoji:Record<string,string>={truck:'🚚',car:'🚗',person:'🚶',bird:'🐦',dog:'🐕',cat:'🐈',trash_truck:'🚛',usps_truck:'🚐'};
function words(label:string) {return label.replaceAll('_',' ').replace(/^./,c=>c.toUpperCase());}
export function Shell({children,preview=false}:{children:React.ReactNode;preview?:boolean}) {
  return <main><header><Link className="brand" href="/" aria-label="Rook home"><span aria-hidden>♜</span> ROOK</Link>
    <span className="privacy">● &nbsp;{preview?'DESIGN PREVIEW':'PRIVATE YARD'}</span></header>{children}
    <footer>Made for a little more awareness. <span>Rook / Yard sentinel</span></footer></main>;
}
export function SampleScene({kind}:{kind:string}) {
  return <svg className="scene" viewBox="0 0 320 180" role="img" aria-label={`Illustration of a ${kind} observation`}>
    <rect width="320" height="180" fill="#30383a"/><path d="M0 100 Q90 75 160 94 T320 80 V180 H0" fill="#4b5147"/>
    <path d="M0 122L320 94V138L0 169Z" fill="#727574"/><path d="M0 146L320 115" stroke="#b9b4a0" strokeWidth="2" strokeDasharray="24 16"/>
    <path d="M31 0L42 127M6 20L37 53L77 8M282 0L277 107M249 10L279 37L315 17" stroke="#1c2524" strokeWidth="13"/>
    {kind==='truck'?<g fill="#c0c4bc"><rect x="132" y="81" width="80" height="33" rx="3"/><path d="M212 88h20l13 13v13h-33z"/><circle cx="150" cy="115" r="8" fill="#171e20"/><circle cx="227" cy="115" r="8" fill="#171e20"/></g>:
      kind==='bird'?<path d="M135 92q14-18 26-8l12-4-3 12q-14 22-35 0l-16 3z" fill="#c1c8bf"/>:
      <g stroke="#c3c7bc" strokeWidth="6" strokeLinecap="round"><circle cx="178" cy="79" r="7" fill="#c3c7bc" stroke="none"/><path d="M178 92l-4 21-13 17m13-17 13 17m-10-33-13 10m13-10 11 12"/></g>}
    <rect x="4" y="4" width="312" height="172" rx="5" fill="none" stroke="#ffffff15"/>
  </svg>;
}
export default function FeedView({events,preview=false,lastReceived}:{events:FeedEvent[];preview?:boolean;lastReceived?:string}) {
  return <Shell preview={preview}>
    {preview?<div className="notice">Sample observations only. No yard images are shown here.<Link href="/login">Owner sign in ↗</Link></div>:
      <form action={signOut} className="sign-out"><button>Sign out</button></form>}
    <section className="intro"><p className="eyebrow">A WINDOW INTO YOUR DAY</p><h1>Around the yard<span>.</span></h1>
      <p>Small visitors. Passing traffic. A little more awareness.</p></section>
    <div className="section-heading"><h2>Recent observations <span>{events.length}</span></h2><span>Newest received first</span></div>
    <div className="columns"><section className="feed" aria-label="Recent observations">
      {events.length===0?<div className="empty"><span>◌</span><h2>Your yard has a place here.</h2><p>Once your sensor is connected, its observations will appear in this private feed.</p></div>:
      events.map((e)=>{const d=e.observation.detections[0];const label=d?.label||'motion';return <article className="card" key={e.device_id+e.event_id}>
        <div className="thumbnail">{preview?<SampleScene kind={label}/>:e.observation.image?<img alt="Raw yard observation" src={`/api/events/${e.device_id}/${e.event_id}/image`}/>:<span className="no-image">No image attached</span>}</div>
        <div className="card-copy"><div className="card-top"><span className="object-emoji" aria-hidden>{emoji[label]||'◌'}</span><time dateTime={e.captured_at}>{new Date(e.captured_at).toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit',timeZone:'America/New_York'})} ET</time></div>
          <h3>{d?`${words(label)} observed`:'Movement observed'}</h3><p>{d?`${Math.round(d.confidence*100)}% detector confidence`:'Identity unknown · useful for later review'}</p>
          <details><summary>Observation details</summary><div className="details"><p>Captured {e.captured_at}</p><p>Received {e.received_at}</p><p>Model: {e.observation.model.name}</p><p>Detection is a prediction, not a reviewed label.</p>
            {e.observation.detections.slice(1).map((box,i)=><p key={i}>{words(box.label)} · {Math.round(box.confidence*100)}%</p>)}</div></details>
        </div></article>;})}
    </section><aside id="device"><p className="eyebrow">YOUR SENSOR</p><div className="sensor-icon">♜</div><h2>Yard sentinel</h2>
      <p>{preview?'A sample of what your sensor will share.':lastReceived?'Observations are reaching your private feed.':'Waiting for the first observation.'}</p>
      <div className="sensor-status"><span className="dot"/>{preview?'Preview · no live connection':lastReceived?'Last received':'Not connected yet'}</div>
      {lastReceived&&<time>{lastReceived}</time>}<hr/><p className="fine">A recent observation confirms delivery at that time. It does not prove the sensor is currently online.</p>
      <p className="fine">A vehicle sighting does not confirm a package delivery.</p></aside></div>
  </Shell>;
}
