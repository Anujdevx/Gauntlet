/**
 * Indeterminate progress indicator.
 *
 * Two modes, because the distinction matters. Bare, it is decorative and hidden
 * from assistive technology; the parent owns the announcement. With a `label` it
 * becomes a live region, which is the only way a screen-reader user learns that
 * a run is in flight rather than that the page has gone silent.
 *
 * The rotation respects `prefers-reduced-motion` via `motion-reduce`, matching
 * the blanket rule in globals.css. A spinner is the one thing on the page that
 * animates continuously during a long run, so it is the most likely of anything
 * here to trigger a vestibular response.
 */
import { Loader2 } from 'lucide-react';

import { cn } from '@/lib/cn';

export interface SpinnerProps {
  /** Announced by the spinner itself. Omit when the parent already announces. */
  readonly label?: string;
  readonly className?: string;
}

const ICON_CLASS = 'size-4 animate-spin motion-reduce:animate-none';

export function Spinner({ label, className }: SpinnerProps): JSX.Element {
  if (label === undefined) {
    return (
      <span className={cn('inline-flex items-center', className)} aria-hidden>
        <Loader2 className={ICON_CLASS} />
      </span>
    );
  }
  return (
    <span className={cn('inline-flex items-center', className)} role="status" aria-live="polite">
      <Loader2 className={ICON_CLASS} />
      <span className="sr-only">{label}</span>
    </span>
  );
}
