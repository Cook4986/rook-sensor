import type {Metadata} from 'next';
import './style.css';
export const metadata:Metadata={title:'Rook · Your yard, observed',description:'Your private yard activity feed',robots:{index:false,follow:false}};
export default function Layout({children}:{children:React.ReactNode}) {
  return <html lang="en"><body>{children}</body></html>;
}
