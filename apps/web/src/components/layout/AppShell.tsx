'use client';

/**
 * Shared page chrome: header, primary navigation, command palette and session.
 *
 * It also resolves the session once for the whole app and owns the global ⌘K
 * binding, so every page gets both without repeating either.
 */
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { LogOut, Search } from 'lucide-react';
import type { ReactNode } from 'react';

import { cn } from '@/lib/cn';
import { Logo } from '@/components/brand/Logo';
import { CommandPalette } from '@/components/layout/CommandPalette';
import { Button, buttonVariants } from '@/components/ui/Button';
import { Kbd } from '@/components/ui/Kbd';
import { ThemeToggle } from '@/components/ui/ThemeToggle';
import { PALETTE_EVENT } from '@/lib/shortcuts';
import { useSessionStore } from '@/store/session';

const NAV_LINKS: ReadonlyArray<{ readonly href: string; readonly label: string }> = [
  { href: '/challenges', label: 'Challenges' },
  { href: '/dashboard', label: 'Skills' },
];

export interface AppShellProps {
  readonly children: ReactNode;
  /** Full-bleed pages (the IDE) manage their own height and padding. */
  readonly bleed?: boolean;
}

export function AppShell({ children, bleed }: AppShellProps): JSX.Element {
  const pathname = usePathname();
  const router = useRouter();
  const user = useSessionStore((state) => state.user);
  const bootstrap = useSessionStore((state) => state.bootstrap);
  const signOut = useSessionStore((state) => state.signOut);
  const [signingOut, setSigningOut] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
    };
    // Monaco swallows ⌘K as a chord prefix, so the editor re-emits it as an event.
    const onEvent = (): void => setPaletteOpen((open) => !open);
    window.addEventListener('keydown', onKey);
    window.addEventListener(PALETTE_EVENT, onEvent);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener(PALETTE_EVENT, onEvent);
    };
  }, []);

  const onSignOut = async (): Promise<void> => {
    setSigningOut(true);
    await signOut();
    setSigningOut(false);
    router.push('/');
  };

  return (
    <div className={cn('flex min-h-screen flex-col', bleed ? 'h-screen overflow-hidden' : '')}>
      <header className="glass sticky top-0 z-40 shrink-0 border-b border-edge">
        <div
          className={cn(
            'flex items-center gap-4 px-4',
            bleed ? 'h-12' : 'mx-auto h-14 max-w-6xl',
          )}
        >
          <Link href="/" aria-label="Gauntlet home" className="rounded-md">
            <Logo />
          </Link>

          <nav aria-label="Primary" className="ml-2 flex items-center gap-0.5">
            {NAV_LINKS.map((link) => {
              const active = pathname === link.href || pathname.startsWith(`${link.href}/`);
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  aria-current={active ? 'page' : undefined}
                  className={cn(
                    'relative rounded-md px-3 py-1.5 text-sm transition-colors motion-reduce:transition-none',
                    active
                      ? 'font-medium text-content'
                      : 'text-content-muted hover:bg-surface-overlay hover:text-content',
                  )}
                >
                  {link.label}
                  {active ? (
                    <span
                      aria-hidden
                      className="absolute inset-x-3 -bottom-[13px] h-0.5 rounded-full bg-accent"
                    />
                  ) : null}
                </Link>
              );
            })}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={() => setPaletteOpen(true)}
              className="hidden h-8 items-center gap-2 rounded-lg border border-edge bg-surface-raised px-2.5 text-xs text-content-subtle transition-colors hover:border-edge-strong hover:text-content-muted sm:inline-flex"
            >
              <Search aria-hidden className="size-3.5" />
              Search
              <Kbd keys={['mod', 'K']} />
            </button>

            <ThemeToggle />

            {user === null ? (
              <Link href="/login" className={buttonVariants({ variant: 'primary', size: 'sm' })}>
                Sign in
              </Link>
            ) : (
              <>
                <span
                  title={user.email}
                  className="hidden size-8 items-center justify-center rounded-full bg-accent/15 text-xs font-semibold uppercase text-accent sm:inline-flex"
                >
                  {user.display_name.slice(0, 1)}
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={signingOut}
                  onClick={() => void onSignOut()}
                  aria-label="Sign out"
                >
                  <LogOut aria-hidden className="size-3.5" />
                  <span className="hidden sm:inline">Sign out</span>
                </Button>
              </>
            )}
          </div>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col">{children}</div>

      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </div>
  );
}
