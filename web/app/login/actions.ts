'use server';
import {redirect} from 'next/navigation';
import {configured,sessionClient} from '@/lib/supabase';
export async function signIn(form:FormData) {
  if(!configured()) redirect('/login?error=unavailable');
  const email=String(form.get('email')||'');const password=String(form.get('password')||'');
  if(email.length>254 || password.length>256) redirect('/login?error=invalid');
  const client=await sessionClient();
  const {error}=await client.auth.signInWithPassword({email,password});
  if(error) redirect('/login?error=invalid');
  redirect('/');
}
export async function signOut() {
  if(configured()) { const client=await sessionClient();await client.auth.signOut(); }
  redirect('/login');
}
