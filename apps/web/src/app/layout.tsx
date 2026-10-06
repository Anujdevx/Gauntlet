/**
 * Root layout.
 *
 * The theme is resolved before first paint by an inline script that reads
 * `localStorage`. Doing this in a React effect would cause a visible flash of
 * the wrong theme, which is especially objectionable in an IDE where a candidate
 * may be sensitive to glare.
 */
import type { Metadata, Viewport } from 'next';
import '@fontsource-variable/inter';
import '@fontsource-variable/jetbrains-mono';
import type { ReactNode } from 'react';

import './globals.css';

export const metadata: Metadata = {
  title: {
    default: 'Gauntlet — adaptive technical assessments',
    template: '%s | Gauntlet',
  },
  description:
    'Practical engineering assessments graded by hidden unit tests, with probabilistic skill tracking.',
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#fcfcfb' },
    { media: '(prefers-color-scheme: dark)', color: '#0b0b0e' },
  ],
};

const THEME_BOOTSTRAP = `
(function () {
  try {
    var stored = localStorage.getItem('gauntlet-theme');
    // Dark unless the candidate explicitly chose light: the IDE is designed dark-first.
    var dark = stored ? stored === 'dark' : true;
    document.documentElement.classList.toggle('dark', dark);
  } catch (e) {
    /* Private browsing or a blocked storage partition: stay on the dark default. */
  }
})();
`.trim();

export default function RootLayout({ children }: { children: ReactNode }): JSX.Element {
  return (
    <html
      lang="en"
      className="dark"
      suppressHydrationWarning
    >
      <head>
        {/*
          Runs before paint on purpose. `dangerouslySetInnerHTML` is safe here
          because the string is a compile-time constant in this module, not any
          kind of user or server data.
        */}
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
      </head>
      <body className="min-h-screen bg-surface text-content">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded focus:bg-surface-raised focus:px-4 focus:py-2 focus:ring-2"
        >
          Skip to main content
        </a>
        <main id="main">{children}</main>
      </body>
    </html>
  );
}
