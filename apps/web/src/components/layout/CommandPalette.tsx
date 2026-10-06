'use client';

/**
 * ⌘K command palette: jump to any page or challenge, flip the theme, and (inside
 * the IDE) trigger Run or Submit. Built on Radix Dialog for focus trapping and
 * Escape handling; the list is a plain listbox driven by arrow keys.
 *
 * Run and Submit are window events rather than imports so the palette does not
 * need to know about the workspace that owns the submission lifecycle.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { usePathname, useRouter } from 'next/navigation';
import {
  CirclePlay,
  CornerDownLeft,
  Gauge,
  Home,
  ListChecks,
  LogIn,
  Moon,
  Search,
  Send,
  Sun,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import { cn } from '@/lib/cn';
import { fetchChallenges } from '@/lib/api';
import { RUN_EVENT, SUBMIT_EVENT } from '@/lib/shortcuts';
import { Kbd } from '@/components/ui/Kbd';
import type { Challenge } from '@/types/api';

interface Command {
  readonly id: string;
  readonly label: string;
  readonly hint?: string;
  readonly group: 'Actions' | 'Go to' | 'Challenges';
  readonly icon: LucideIcon;
  readonly run: () => void;
}

export interface CommandPaletteProps {
  readonly open: boolean;
  readonly onOpenChange: (open: boolean) => void;
}

export function CommandPalette({ open, onOpenChange }: CommandPaletteProps): JSX.Element {
  const router = useRouter();
  const pathname = usePathname();
  const [query, setQuery] = useState('');
  const [cursor, setCursor] = useState(0);
  const [challenges, setChallenges] = useState<readonly Challenge[]>([]);
  const listRef = useRef<HTMLUListElement | null>(null);

  // Load the catalogue lazily the first time the palette opens. A signed-out
  // visitor gets a 401; that just means no challenge entries, not an error.
  useEffect(() => {
    if (!open || challenges.length > 0) return;
    const controller = new AbortController();
    fetchChallenges(controller.signal)
      .then((response) => setChallenges(response.challenges))
      .catch(() => undefined);
    return () => controller.abort();
  }, [open, challenges.length]);

  useEffect(() => {
    if (open) {
      setQuery('');
      setCursor(0);
    }
  }, [open]);

  const close = useCallback(() => onOpenChange(false), [onOpenChange]);

  const commands = useMemo<readonly Command[]>(() => {
    const go = (href: string) => () => {
      close();
      router.push(href);
    };
    const inIde = pathname.startsWith('/challenges/');
    const dark = typeof document !== 'undefined' && document.documentElement.classList.contains('dark');
    const list: Command[] = [];
    if (inIde) {
      list.push(
        {
          id: 'run',
          label: 'Run tests',
          hint: 'mod+Enter',
          group: 'Actions',
          icon: CirclePlay,
          run: () => {
            close();
            window.dispatchEvent(new Event(RUN_EVENT));
          },
        },
        {
          id: 'submit',
          label: 'Submit solution',
          hint: 'mod+shift+Enter',
          group: 'Actions',
          icon: Send,
          run: () => {
            close();
            window.dispatchEvent(new Event(SUBMIT_EVENT));
          },
        },
      );
    }
    list.push({
      id: 'theme',
      label: dark ? 'Switch to light theme' : 'Switch to dark theme',
      group: 'Actions',
      icon: dark ? Sun : Moon,
      run: () => {
        const next = !document.documentElement.classList.contains('dark');
        document.documentElement.classList.toggle('dark', next);
        try {
          localStorage.setItem('gauntlet-theme', next ? 'dark' : 'light');
        } catch {
          /* ignore */
        }
        close();
      },
    });
    list.push(
      { id: 'home', label: 'Home', group: 'Go to', icon: Home, run: go('/') },
      { id: 'challenges', label: 'Challenges', group: 'Go to', icon: ListChecks, run: go('/challenges') },
      { id: 'skills', label: 'Skill matrix', group: 'Go to', icon: Gauge, run: go('/dashboard') },
      { id: 'login', label: 'Sign in', group: 'Go to', icon: LogIn, run: go('/login') },
    );
    for (const challenge of challenges) {
      list.push({
        id: `c:${challenge.slug}`,
        label: challenge.title,
        hint: challenge.language,
        group: 'Challenges',
        icon: ListChecks,
        run: go(`/challenges/${encodeURIComponent(challenge.slug)}`),
      });
    }
    return list;
  }, [challenges, close, pathname, router]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (needle === '') return commands;
    return commands.filter((command) => command.label.toLowerCase().includes(needle));
  }, [commands, query]);

  useEffect(() => setCursor(0), [query]);

  useEffect(() => {
    listRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [cursor]);

  const onKeyDown = (event: React.KeyboardEvent): void => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setCursor((current) => Math.min(filtered.length - 1, current + 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setCursor((current) => Math.max(0, current - 1));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      filtered[cursor]?.run();
    }
  };

  let lastGroup = '';

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm data-[state=open]:animate-fade-up" />
        <Dialog.Content
          aria-describedby={undefined}
          onKeyDown={onKeyDown}
          className="fixed left-1/2 top-[16vh] z-50 w-[min(92vw,34rem)] -translate-x-1/2 overflow-hidden rounded-panel border border-edge-strong bg-surface-overlay shadow-pop data-[state=open]:animate-pop"
        >
          <Dialog.Title className="sr-only">Command palette</Dialog.Title>
          <div className="flex items-center gap-3 border-b border-edge px-4">
            <Search aria-hidden className="size-4 text-content-subtle" />
            <input
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search challenges, pages, actions…"
              aria-label="Search commands"
              role="combobox"
              aria-expanded
              aria-controls="palette-list"
              className="h-12 flex-1 bg-transparent text-sm text-content outline-none placeholder:text-content-subtle focus-visible:ring-0"
            />
            <Kbd keys={['Esc']} />
          </div>

          <ul
            id="palette-list"
            ref={listRef}
            role="listbox"
            className="scrollbar-thin max-h-[50vh] overflow-auto p-2"
          >
            {filtered.length === 0 ? (
              <li className="px-3 py-8 text-center text-sm text-content-subtle">
                Nothing matches “{query}”.
              </li>
            ) : null}
            {filtered.map((command, index) => {
              const header = command.group !== lastGroup ? command.group : null;
              lastGroup = command.group;
              const Icon = command.icon;
              const selected = index === cursor;
              return (
                <li key={command.id} role="presentation">
                  {header !== null ? (
                    <p className="px-3 pb-1 pt-2 text-[11px] font-medium uppercase tracking-wider text-content-subtle">
                      {header}
                    </p>
                  ) : null}
                  <button
                    type="button"
                    role="option"
                    aria-selected={selected}
                    onMouseMove={() => setCursor(index)}
                    onClick={command.run}
                    className={cn(
                      'flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm',
                      selected ? 'bg-accent/15 text-content' : 'text-content-muted',
                    )}
                  >
                    <Icon aria-hidden className={cn('size-4', selected && 'text-accent')} />
                    <span className="flex-1 truncate">{command.label}</span>
                    {command.hint?.includes('+') ? (
                      <Kbd keys={command.hint.split('+')} />
                    ) : command.hint !== undefined ? (
                      <span className="text-xs text-content-subtle">{command.hint}</span>
                    ) : null}
                    {selected ? (
                      <CornerDownLeft aria-hidden className="size-3.5 text-content-subtle" />
                    ) : null}
                  </button>
                </li>
              );
            })}
          </ul>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
