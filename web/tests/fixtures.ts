import {randomUUID} from 'node:crypto';
import sharp from 'sharp';
import {digest} from '../lib/protocol';
export const owner='00000000-0000-4000-8000-000000000001';
export const outsider='00000000-0000-4000-8000-000000000002';
export const token='rook_device_'+'a'.repeat(43);
export async function fixture() {
  const image=await sharp({create:{width:16,height:12,channels:3,background:'#445544'}}).jpeg().toBuffer();
  const event={schema_version:1,event_id:randomUUID(),device_id:'yard-pi',kind:'detection',
    captured_at:'2026-10-12T18:30:00.123456Z',received_at:null,
    model:{name:'fixture',format:'ncnn',artifact_sha256:'a'.repeat(64),classes_sha256:'b'.repeat(64)},
    detections:[{class_id:7,label:'truck',confidence:.9,xyxy:[0,.1,1,.9],subclass:null}],
    image:{sha256:digest(image),media_type:'image/jpeg',byte_length:image.length,width:16,height:12,role:'raw'}};
  const eventJson=JSON.stringify(event);
  return {event,eventJson,eventHash:digest(eventJson),image};
}
