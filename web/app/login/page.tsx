import {Shell} from '../feed-view';
import {configured} from '@/lib/supabase';
import {signIn} from './actions';
export default async function Login({searchParams}:{searchParams:Promise<{error?:string}>}) {
  const {error}=await searchParams;
  return <Shell><section className="login"><p className="eyebrow">OWNER ACCESS</p><h1>Welcome home<span>.</span></h1><p>Your yard stays yours. Sign in with your provisioned owner account.</p>
    {!configured()?<div className="notice">Sign-in will be available when your private connection is ready.</div>:
    <form action={signIn}><label>Email<input name="email" type="email" autoComplete="username" required/></label>
      <label>Password<input name="password" type="password" autoComplete="current-password" required/></label>
      {error&&<p role="alert">Unable to sign in. Check your details and try again.</p>}<button>Open my yard →</button></form>}
    <p className="fine">Access is invite-only. There is no public registration.</p></section></Shell>;
}
