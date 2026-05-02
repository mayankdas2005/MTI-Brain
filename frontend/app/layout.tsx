import type { Metadata } from 'next'
import Script from 'next/script'
import { Geist, Geist_Mono } from 'next/font/google'
import { Analytics } from '@vercel/analytics/next'
import { Providers } from '@/components/providers'
import './globals.css'

const geist = Geist({
  subsets: ["latin"],
  variable: "--font-geist",
  display: "swap",
  preload: true,
});
const geistMono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-geist-mono",
  display: "swap",
  preload: true,
});

const apiOrigin = (() => {
  const raw = process.env.NEXT_PUBLIC_API_URL;
  if (!raw) return null;
  try {
    return new URL(raw).origin;
  } catch {
    return null;
  }
})();

export const metadata: Metadata = {
  title: 'MTI Brain',
  icons: {
    icon: [
      { url: '/icon-192.png', type: 'image/png', sizes: '192x192' },
      { url: '/icon-512.png', type: 'image/png', sizes: '512x512' },
    ],
    apple: '/apple-icon.png',
    shortcut: '/icon-192.png',
  },
  manifest: '/manifest.json',
  applicationName: 'MTI Brain',
  appleWebApp: { capable: true, title: 'MTI Brain', statusBarStyle: 'default' },
};

export const viewport = {
  themeColor: '#0a3a73',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {apiOrigin ? (
          <>
            <link rel="preconnect" href={apiOrigin} />
            <link rel="dns-prefetch" href={apiOrigin} />
          </>
        ) : null}
        {/* Capture `beforeinstallprompt` before any React bundle loads. Edge
            in particular fires this event very early; if our store's
            listener attaches after parse, we miss the event entirely and
            never offer install. The captured event is read by
            lib/store/install.ts on init. */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              "window.__questPwaPrompt=null;" +
              "window.addEventListener('beforeinstallprompt',function(e){" +
              "e.preventDefault();window.__questPwaPrompt=e;" +
              "});" +
              "window.addEventListener('appinstalled',function(){" +
              "window.__questPwaPrompt=null;window.__questPwaInstalled=true;" +
              "});",
          }}
        />
      </head>
      <body className={`${geist.variable} ${geistMono.variable} font-sans antialiased`}>
        <Providers>
          {children}
        </Providers>
        <Analytics />
        <Script id="mti-easter-egg" strategy="afterInteractive" dangerouslySetInnerHTML={{ __html: `
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
      </body>
    </html>
  )
}
