import {digest} from './protocol';
function canonical(value:any):string {
  if(Array.isArray(value)) return '['+value.map(canonical).join(',')+']';
  if(value && typeof value==='object') return '{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+canonical(value[k])).join(',')+'}';
  return JSON.stringify(value);
}
export function verifyManifest(manifest:any) {
  if(!manifest || typeof manifest!=='object' || Array.isArray(manifest)) throw new Error('Invalid manifest');
  const {model,class_names:names,artifact_files:files}=manifest;
  if(!model || Object.keys(model).sort().join(',')!=='artifact_sha256,classes_sha256,format,name' ||
     typeof model.name!=='string' || !model.name.trim() || model.name.length>128 || /[\x00-\x1f]/.test(model.name)) throw new Error('Invalid model identity');
  if(!model || !Array.isArray(names) || ![80,110].includes(names.length) ||
     names.some(n=>typeof n!=='string'||!n||n.length>64) || digest(canonical(names))!==model.classes_sha256) throw new Error('Vocabulary mismatch');
  if(!Array.isArray(files)||!files.length||files.length>1000) throw new Error('Invalid artifact files');
  let total=0;const paths=new Set<string>();
  for(const f of files) {
    if(!f || typeof f!=='object' || Object.keys(f).sort().join(',')!=='bytes,path,sha256' || typeof f.path!=='string' ||
       !f.path || f.path.startsWith('/') || f.path.split('/').some((s:string)=>!s||s==='.'||s==='..') ||
       paths.has(f.path) || !Number.isSafeInteger(f.bytes) || f.bytes<0 || !/^[a-f0-9]{64}$/.test(f.sha256)) throw new Error('Invalid artifact file');
    paths.add(f.path);total+=f.bytes;
  }
  if(total>512*1024*1024) throw new Error('Artifact too large');
  if(model.format==='ncnn' && (!files.some(f=>f.path.endsWith('.bin'))||!files.some(f=>f.path.endsWith('.param')))) throw new Error('Missing NCNN graph or weights');
  if(!['ncnn','pytorch','onnx'].includes(model.format)) throw new Error('Unknown format');
  const hash=model.format==='ncnn'?digest(canonical(files)):files.length===1?files[0].sha256:null;
  if(hash!==model.artifact_sha256) throw new Error('Artifact digest mismatch');
  return manifest;
}
