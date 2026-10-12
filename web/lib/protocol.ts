import { createHash } from 'node:crypto';
import sharp from 'sharp';

export class Denied extends Error {
  constructor(public status: number, message: string) { super(message); }
}
export const digest = (value: string | Buffer) => createHash('sha256').update(value).digest('hex');
export const MAX_BODY = 2 * 1024 * 1024 * 4 / 3 + 160 * 1024;
const fail = () => { throw new Denied(422, 'Invalid observation'); };
function object(value: unknown, fields: string): asserts value is Record<string, any> {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).sort().join(' ') !== fields.split(' ').sort().join(' ')) fail();
}
function text(v: unknown, length = 128) {
  if (typeof v !== 'string' || !v.trim() || v.length > length || /[\x00-\x1f]/.test(v)) fail();
}
function hash(v: unknown) { if (typeof v !== 'string' || !/^[a-f0-9]{64}$/.test(v)) fail(); }
function unit(v: unknown) { if (typeof v !== 'number' || !Number.isFinite(v) || v < 0 || v > 1) fail(); }
export const devicePattern = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
export const eventPattern = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
export function validateEvent(value: unknown) {
  object(value, 'schema_version event_id device_id kind captured_at received_at model detections image');
  const e = value;
  if (e.schema_version !== 1 || typeof e.event_id !== 'string' || !eventPattern.test(e.event_id) ||
      typeof e.device_id !== 'string' || !devicePattern.test(e.device_id) || e.received_at !== null) fail();
  if (typeof e.captured_at !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z$/.test(e.captured_at) ||
      !Number.isFinite(Date.parse(e.captured_at))) fail();
  if(new Date(e.captured_at).toISOString().slice(0,19)!==e.captured_at.slice(0,19)) fail();
  object(e.model, 'name format artifact_sha256 classes_sha256');
  text(e.model.name); hash(e.model.artifact_sha256); hash(e.model.classes_sha256);
  if (!['ncnn','pytorch','onnx'].includes(e.model.format)) fail();
  if (!Array.isArray(e.detections) || e.detections.length > 200 ||
      e.kind !== (e.detections.length ? 'detection' : 'motion')) fail();
  for (const d of e.detections) {
    object(d, 'class_id label confidence xyxy subclass');
    if (!Number.isInteger(d.class_id) || d.class_id < 0 || d.class_id > 109 || d.class_id === 107) fail();
    text(d.label,64); unit(d.confidence);
    if (!Array.isArray(d.xyxy) || d.xyxy.length !== 4) fail();
    d.xyxy.forEach(unit);
    if (d.xyxy[0] >= d.xyxy[2] || d.xyxy[1] >= d.xyxy[3]) fail();
    if (d.subclass !== null) { object(d.subclass,'label source confidence'); text(d.subclass.label,64); text(d.subclass.source); unit(d.subclass.confidence); }
  }
  if (e.image !== null) {
    object(e.image,'sha256 media_type byte_length width height role');
    hash(e.image.sha256);
    if (e.image.media_type !== 'image/jpeg' || e.image.role !== 'raw' ||
        !Number.isInteger(e.image.byte_length) || e.image.byte_length < 1 || e.image.byte_length > 2*1024*1024) fail();
    for (const n of [e.image.width,e.image.height]) if (!Number.isInteger(n) || n < 1 || n > 16384) fail();
  }
  return e;
}
export async function readLimited(request: Request) {
  if (!request.body) throw new Denied(400,'Missing body');
  if (Number(request.headers.get('content-length')) > MAX_BODY) throw new Denied(413,'Observation too large');
  const reader = request.body.getReader(); const chunks: Uint8Array[] = []; let size = 0;
  try { while (true) {
    const part = await reader.read(); if (part.done) break;
    size += part.value.length;
    if (size > MAX_BODY) { await reader.cancel(); throw new Denied(413,'Observation too large'); }
    chunks.push(part.value);
  } } finally { reader.releaseLock(); }
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch { throw new Denied(400,'Invalid JSON'); }
}
export async function parseEnvelope(value: unknown) {
  object(value,'event_json image_base64');
  if (typeof value.event_json !== 'string' || Buffer.byteLength(value.event_json) > 65536) fail();
  let decoded; try { decoded = JSON.parse(value.event_json); } catch { return fail(); }
  const event = validateEvent(decoded);
  let image: Buffer | null = null;
  if (event.image === null) { if (value.image_base64 !== null) fail(); }
  else {
    if (typeof value.image_base64 !== 'string' || value.image_base64.length > 2796204) fail();
    image = Buffer.from(value.image_base64, 'base64');
    if (image.toString('base64') !== value.image_base64 || image.length !== event.image.byte_length || digest(image) !== event.image.sha256) fail();
    try {
      const decoder = sharp(image, {limitInputPixels: 8388608, failOn: 'warning'});
      const metadata = await decoder.metadata();
      if (metadata.format !== 'jpeg' || metadata.width !== event.image.width || metadata.height !== event.image.height ||
          (metadata.orientation && metadata.orientation !== 1)) fail();
      await decoder.raw().toBuffer(); // Decode all pixels; headers alone aren't proof.
    } catch { fail(); }
  }
  return {event, eventJson:value.event_json as string, eventHash:digest(value.event_json), image};
}
