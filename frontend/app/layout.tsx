import type { Metadata } from 'next'
import { Geist, Geist_Mono } from 'next/font/google'
import { Providers } from '@/components/providers'
import './globals.css'

const geist = Geist({ subsets: ["latin"], variable: "--font-geist" });
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono" });

export const metadata: Metadata = {
  title: 'MTI Brain',
  icons: { icon: '/favicon.ico' },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
  <script dangerouslySetInnerHTML={{ __html: `
    console.log("%c" +
      "███╗   ███╗████████╗██╗    \\n" +
      "████╗ ████║╚══██╔══╝██║    \\n" +
      "██╔████╔██║   ██║   ██║    \\n" +
      "██║╚██╔╝██║   ██║   ██║    \\n" +
      "██║ ╚═╝ ██║   ██║   ██║    \\n" +
      "╚═╝     ╚═╝   ╚═╝   ╚═╝    \\n" +
      "                            \\n" +
      "██████╗ ██████╗  █████╗ ██╗███╗   ██╗\\n" +
      "██╔══██╗██╔══██╗██╔══██╗██║████╗  ██║\\n" +
      "██████╔╝██████╔╝███████║██║██╔██╗ ██║\\n" +
      "██╔══██╗██╔══██╗██╔══██║██║██║╚██╗██║\\n" +
      "██████╔╝██║  ██║██║  ██║██║██║ ╚████║\\n" +
      "╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝╚═╝  ╚═══╝",
      "font-family:monospace;color:#184B9B;font-size:10px"
    );
    console.log(
      "%c🧠 Hey, a curious one! Just like Milestone Technologies engineers every\\n" +
      "   solution to spec - this app was built just for you.\\n" +
      "   No two problems are exactly alike.\\n\\n" +
      "   Fun fact: Milestone Technologies has been powering smarter IT since 1997.\\n" +
      "   This easter egg has been here since... you found it. Welcome aboard.\\n\\n" +
      "   Next stop: doing something amazing today. MTI Brain is online. ⚡",
      "color:#6b7a8d;font-size:11px;line-height:1.6"
    );
  `}} />
</head>
      <body className={`${geist.variable} ${geistMono.variable} font-sans antialiased`}>
        <Providers>
          {children}
        </Providers>
      </body>
    </html>
  )
}
