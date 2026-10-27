import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = { title: 'Naija Bites | Order something delicious', description: 'Your food, just a chat away.' };
export default function RootLayout({children}:{children:React.ReactNode}) { return <html lang="en"><body>{children}</body></html>; }
