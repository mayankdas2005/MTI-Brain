import type { Metadata, Viewport } from 'next'
import Script from 'next/script'
import { Providers } from '@/components/providers'
import './globals.css'

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

export const viewport: Viewport = {
  themeColor: '#0a3a73',
  width: 'device-width',
  initialScale: 1,
  maximumScale: 5,
  viewportFit: 'cover',
  userScalable: true,
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" crossOrigin="anonymous" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Manrope:wght@300;400;500;600;700;800&family=IBM+Plex+Mono:wght@400;500&display=swap" />
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
            lib/store/install.ts on init.

            Using `next/script` with `strategy="beforeInteractive"` so the
            tag is hoisted into the real document <head> before hydration -
            a raw <script> rendered as a React element doesn't execute on
            the client (React 19 / Next 16 warning). */}
        <Script
          id="mti-brain-pwa-prompt-capture"
          strategy="beforeInteractive"
          dangerouslySetInnerHTML={{
            __html:
              "window.__mtiBrainPwaPrompt=null;" +
              "window.addEventListener('beforeinstallprompt',function(e){" +
              "e.preventDefault();window.__mtiBrainPwaPrompt=e;" +
              "});" +
              "window.addEventListener('appinstalled',function(){" +
              "window.__mtiBrainPwaPrompt=null;window.__mtiBrainPwaInstalled=true;" +
              "});",
          }}
        />
      </head>
      <body className="font-sans antialiased" suppressHydrationWarning>
        <Providers>
          {children}
        </Providers>
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
      "%c🧠 Hey, curious one! Just as Milestone Technologies engineers every solution to spec — this app was built just for you.\\n" +
      "   No two problems are exactly alike.\\n\\n" +
      "   Fun fact: Milestone Technologies has been powering smarter IT since 1997.\\n" +
      "   This Easter egg has been here since... you found it. Welcome aboard.\\n\\n" +
      "   Next stop: do something amazing today. MTI Brain is online. ⚡",
      "color:#6b7a8d;font-size:11px;line-height:1.6"
    );
  `}} />
      </body>
    </html>
  )
}
