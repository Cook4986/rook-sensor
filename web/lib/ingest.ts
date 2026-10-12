import { Denied, digest, parseEnvelope, readLimited } from './protocol';
export type Receipt = {schema_version:number; device_id:string; event_id:string; event_sha256:string; received_at:string};
export type IngestStore = {
  authenticate(hash:string): Promise<string | null>;
  commit(hash:string, observation:Awaited<ReturnType<typeof parseEnvelope>>): Promise<Receipt>;
};
export const privateHeaders = {'Cache-Control':'private, no-store', 'Vary':'Cookie', 'X-Content-Type-Options':'nosniff'};
export async function handleIngest(request:Request, store:IngestStore) {
  try {
    const match = /^Bearer (rook_device_[A-Za-z0-9_-]{43})$/.exec(request.headers.get('authorization') || '');
    if (!match) throw new Denied(401,'Device authentication required');
    const tokenHash = digest(match[1]); const device = await store.authenticate(tokenHash);
    if (!device) throw new Denied(401,'Device authentication required');
    if (request.headers.get('content-type')?.split(';')[0] !== 'application/json') throw new Denied(415,'JSON required');
    const observation = await parseEnvelope(await readLimited(request));
    if (observation.event.device_id !== device) throw new Denied(403,'Device mismatch');
    if (request.headers.get('idempotency-key') !== device+'/'+observation.event.event_id) throw new Denied(422,'Invalid idempotency key');
    const receipt = await store.commit(tokenHash, observation);
    return Response.json(receipt,{status:200,headers:privateHeaders});
  } catch (error) {
    const known = error instanceof Denied;
    return Response.json({error:known ? error.message : 'Ingestion unavailable'}, {status:known ? error.status : 503,headers:privateHeaders});
  }
}
