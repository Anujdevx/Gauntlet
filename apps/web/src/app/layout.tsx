/**
 * Root layout.
 *
 * The theme is resolved before first paint by an inline script that reads
 * `localStorage`. Doing this in a React effect would cause a visible flash of
 * the wrong theme, which is especially objectionable in an IDE where a candidate
 * may be sensitive to glare.
 */
import type { Metadata, Viewport } from 'next';
import type { ReactNode } from 'react';

import './globals.css';

export const metadata: Metadata = {
  title: {
    default: 'Adaptive Assessment Simulator',
    template: '%s | Adaptive Assessment Simulator',
  },
  description:
    'Practical engineering assessments graded by hidden unit tests, with probabilistic skill tracking.',
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#ffffff' },
    { media: '(prefers-color-scheme: dark)', color: '#0a0b0d' },
  ],
};

const THEME_BOOTSTRAP = `
(function () {
  try {
    var stored = localStorage.getItem('oa-theme');
    var dark = stored
      ? stored === 'dark'
      : window.matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.classList.toggle('dark', dark);
  } catch (e) {
    /* Private browsing or a blocked storage partition: fall back to light. */
  }
})();
`.trim();

export default function RootLayout({ children }: { children: ReactNode }): JSX.Element {
  return (
    <html lang="en" suppressHydrationWarning>
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
