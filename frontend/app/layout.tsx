import type { Metadata } from 'next';
import './globals.css';
import { AuthProvider } from './auth-provider';
export const metadata: Metadata = { title: 'Naija Bites | Order something delicious', description: 'Your food, just a chat away.' };
export default function RootLayout({children}:{children:React.ReactNode}) { return <html lang="en"><body><AuthProvider>{children}</AuthProvider></body></html>; }
