/**
 * Gauntlet mark: three gates in ascending height, the last lit in ember.
 * It reads as both a gauntlet of checkpoints and a rising mastery bar chart.
 */
import { cn } from '@/lib/cn';

export function LogoMark({ className }: { readonly className?: string }): JSX.Element {
  return (
    <svg
      viewBox="0 0 32 32"
      aria-hidden
      className={cn('size-7 shrink-0', className)}
      fill="none"
    >
      <rect width="32" height="32" rx="9" className="fill-surface-overlay" />
      <rect x="0.5" y="0.5" width="31" height="31" rx="8.5" className="stroke-edge-strong" />
      <rect x="7" y="16" width="4.5" height="9" rx="1.5" className="fill-content-subtle" />
      <rect x="13.75" y="11" width="4.5" height="14" rx="1.5" className="fill-content-muted" />
      <rect x="20.5" y="6" width="4.5" height="19" rx="1.5" className="fill-accent" />
    </svg>
  );
}

export function Logo({ className }: { readonly className?: string }): JSX.Element {
  return (
    <span className={cn('inline-flex items-center gap-2.5', className)}>
      <LogoMark />
      <span className="text-[15px] font-semibold tracking-tight">Gauntlet</span>
    </span>
  );
}
