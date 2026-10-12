import {createServerClient} from '@supabase/ssr';
import {NextRequest,NextResponse} from 'next/server';
export async function proxy(request:NextRequest) {
  let response=NextResponse.next({request});
  response.headers.set('Cache-Control','private, no-store');
  if(!process.env.SUPABASE_URL || !process.env.SUPABASE_PUBLISHABLE_KEY) return response;
  const client=createServerClient(process.env.SUPABASE_URL,process.env.SUPABASE_PUBLISHABLE_KEY,{
    cookieOptions:{httpOnly:true,secure:process.env.NODE_ENV==='production',sameSite:'lax',path:'/'},
    cookies:{getAll:()=>request.cookies.getAll(),setAll:(values)=>{
      values.forEach(({name,value})=>request.cookies.set(name,value));
      response=NextResponse.next({request});
      values.forEach(({name,value,options})=>response.cookies.set(name,value,options));
      response.headers.set('Cache-Control','private, no-store');
    }}
  });
  await client.auth.getClaims();
  return response;
}
export const config={matcher:['/','/login','/api/events/:path*']};
