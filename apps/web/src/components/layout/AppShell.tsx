'use client';

/**
 * Shared page chrome: header, primary navigation, and the session affordance.
 *
 * Every data page needs the same shell, and duplicating it is how pages end up
 * with inconsistent focus order and two different sets of nav links. It also
 * resolves the session once for the whole app, so navigating between pages does
 * not re-probe `/auth/me` on every render.
 */
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { LogOut, User as UserIcon } from 'lucide-react';

import { cn } from '@/lib/cn';
import { Button, buttonVariants } from '@/components/ui/Button';
import { useSessionStore } from '@/store/session';
import type { ReactNode } from 'react';

const NAV_LINKS: ReadonlyArray<{ readonly href: string; readonly label: string }> = [
  { href: '/challenges', label: 'Challenges' },
  { href: '/dashboard', label: 'Skill matrix' },
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

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  const onSignOut = async (): Promise<void> => {
    setSigningOut(true);
    await signOut();
    setSigningOut(false);
    router.push('/');
  };

  return (
    <div className={cn('flex min-h-screen flex-col', bleed ? 'h-screen overflow-hidden' : '')}>
      <header className="shrink-0 border-b border-edge bg-surface-raised">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-4 px-4">
          <Link href="/" className="flex items-center gap-2 text-sm font-semibold">
            <span aria-hidden className="size-2 rounded-full bg-verdict-pass" />
            Assessment Simulator
          </Link>

          <nav aria-label="Primary" className="flex items-center gap-1">
            {NAV_LINKS.map((link) => {
              const active = pathname === link.href || pathname.startsWith(`${link.href}/`);
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  aria-current={active ? 'page' : undefined}
                  className={cn(
                    'rounded px-2.5 py-1.5 text-sm transition-colors motion-reduce:transition-none',
                    active
                      ? 'bg-surface-sunken font-medium text-content'
                      : 'text-content-muted hover:bg-surface-sunken hover:text-content',
                  )}
                >
                  {link.label}
                </Link>
              );
            })}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            {user === null ? (
              <Link href="/login" className={buttonVariants({ variant: 'primary', size: 'sm' })}>
                Sign in
              </Link>
            ) : (
              <>
                <span className="hidden items-center gap-1.5 text-sm text-content-muted sm:flex">
                  <UserIcon aria-hidden className="size-3.5" />
                  {user.display_name}
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={signingOut}
                  onClick={() => void onSignOut()}
                >
                  <LogOut aria-hidden className="size-3.5" />
                  Sign out
                </Button>
              </>
            )}
          </div>
        </div>
      </header>

      <div className={cn('min-h-0 flex-1', bleed ? 'flex flex-col' : '')}>{children}</div>
    </div>
  );
}
