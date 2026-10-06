/**
 * Compact status and metadata chip.
 *
 * Tones are named rather than accepting a colour class, so a verdict chip can
 * never be given a colour that contradicts its meaning. Callers always pair a
 * tone with a text label: colour alone is unreadable to a colour-blind candidate.
 */
import { cva } from 'class-variance-authority';
import type { HTMLAttributes, ReactNode } from 'react';

import { cn } from '@/lib/cn';

export type BadgeTone = 'neutral' | 'accent' | 'pass' | 'fail' | 'skip' | 'infra';

const badgeVariants = cva(
  'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium whitespace-nowrap',
  {
    variants: {
      tone: {
        neutral: 'border-edge bg-surface-sunken text-content-muted',
        accent: 'border-accent/40 bg-accent/10 text-accent',
        pass: 'border-verdict-pass/40 bg-verdict-pass/10 text-verdict-pass',
        fail: 'border-verdict-fail/40 bg-verdict-fail/10 text-verdict-fail',
        skip: 'border-verdict-skip/40 bg-verdict-skip/10 text-verdict-skip',
        infra: 'border-verdict-infra/40 bg-verdict-infra/10 text-verdict-infra',
      },
    },
    defaultVariants: { tone: 'neutral' },
  },
);

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  readonly tone?: BadgeTone;
  readonly children: ReactNode;
}

export function Badge({ tone, className, children, ...rest }: BadgeProps): JSX.Element {
  return (
    <span className={cn(badgeVariants({ tone }), className)} {...rest}>
      {children}
    </span>
  );
}
