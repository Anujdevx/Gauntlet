/**
 * Button and its variant recipe.
 *
 * The recipe is exported separately because the header and the IDE toolbar need
 * button-shaped links, and passing the recipe to a `next/link` gives identical
 * styling without polymorphic-ref casts.
 */
import { cva } from 'class-variance-authority';
import type { ButtonHTMLAttributes } from 'react';

import { cn } from '@/lib/cn';

export const buttonVariants = cva(
  'inline-flex select-none items-center justify-center gap-2 whitespace-nowrap rounded-lg border text-sm font-medium transition-all duration-150 motion-reduce:transition-none active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        primary:
          'border-transparent bg-accent text-accent-fg shadow-glow-sm hover:brightness-110 hover:shadow-glow',
        secondary:
          'border-edge bg-surface-raised text-content hover:border-edge-strong hover:bg-surface-overlay',
        ghost:
          'border-transparent bg-transparent text-content-muted hover:bg-surface-overlay hover:text-content',
        danger:
          'border-verdict-fail/40 bg-verdict-fail/10 text-verdict-fail hover:bg-verdict-fail/20',
      },
      size: {
        sm: 'h-8 px-3 text-xs',
        md: 'h-10 px-4',
        lg: 'h-12 px-6 text-[15px]',
      },
    },
    defaultVariants: { variant: 'secondary', size: 'md' },
  },
);

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';
export type ButtonSize = 'sm' | 'md' | 'lg';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  readonly variant?: ButtonVariant;
  readonly size?: ButtonSize;
}

export function Button({ variant, size, className, type, ...rest }: ButtonProps): JSX.Element {
  return (
    <button
      // A bare `<button>` inside a form submits it; default to `button`.
      type={type ?? 'button'}
      className={cn(buttonVariants({ variant, size }), className)}
      {...rest}
    />
  );
}
