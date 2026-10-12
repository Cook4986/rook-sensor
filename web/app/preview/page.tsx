import FeedView from '../feed-view';
export default function Preview() {
  const events=['truck','bird','person'].map((label,i)=>({device_id:'sample-sensor',event_id:`sample-${i}`,
    captured_at:`2026-10-12T${17-i}:24:00Z`,received_at:`2026-10-12T${17-i}:24:02Z`,
    observation:{kind:'detection',model:{name:'Example stock detector'},image:null,detections:[{label,confidence:[.92,.81,.96][i]}]}}));
  return <FeedView events={events} preview/>;
}
