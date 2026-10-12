import {Denied,devicePattern,eventPattern} from './protocol';
import {privateHeaders} from './ingest';
export function errorResponse(error:unknown) {
  return Response.json({error:error instanceof Denied ? error.message : 'Private feed unavailable'},
    {status:error instanceof Denied ? error.status : 503,headers:privateHeaders});
}
export function validIdentity(device:string,event:string) {
  if(!devicePattern.test(device) || !eventPattern.test(event)) throw new Denied(404,'Image unavailable');
}
export type FeedEvent={device_id:string;event_id:string;captured_at:string;received_at:string;
  observation:{kind:string;model:{name:string};image:unknown;detections:{label:string;confidence:number}[]}};
