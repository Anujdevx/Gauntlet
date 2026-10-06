/** Keyboard hint chip. Shows ⌘ on macOS and Ctrl elsewhere, after hydration. */
'use client';

import { useEffect, useState } from 'react';

import { cn } from '@/lib/cn';

/** True on Apple platforms. Resolved in an effect so server and client first paint match. */
export function useIsMac(): boolean {
  const [mac, setMac] = useState(true);
  useEffect(() => {
    setMac(/Mac|iPhone|iPad/i.test(navigator.platform));
  }, []);
  return mac;
}

/** `keys` use `mod` for the platform modifier, e.g. `['mod', 'Enter']`. */
export function Kbd({
  keys,
  className,
}: {
  readonly keys: readonly string[];
  readonly className?: string;
}): JSX.Element {
  const mac = useIsMac();
  const label = (key: string): string => {
    if (key === 'mod') return mac ? '⌘' : 'Ctrl';
    if (key === 'shift') return mac ? '⇧' : 'Shift';
    if (key === 'Enter') return '↵';
    return key;
  };
  return (
    <span className={cn('inline-flex items-center gap-0.5', className)}>
      {keys.map((key) => (
        <kbd
          key={key}
          className="inline-flex min-w-[1.25rem] items-center justify-center rounded border border-edge-strong bg-surface-sunken px-1 py-px font-mono text-[10px] leading-4 text-content-muted"
        >
          {label(key)}
        </kbd>
      ))}
    </span>
  );
}
