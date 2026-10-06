/**
 * Button and its variant recipe.
 *
 * The recipe is exported separately from the component because the header and
 * the IDE toolbar need button-shaped links, and `asChild`-style polymorphism
 * would add a `ref` type cast to every call site. Passing the recipe to a
 * `next/link` gives identical styling with none of that.
 */
import { cva } from 'class-variance-authority';
import type { ButtonHTMLAttributes } from 'react';

import { cn } from '@/lib/cn';

/**
 * Variants are string unions only.
 *
 * A boolean variant would be convenient for `block`, but `cva` derives variant
 * keys from the object and the resulting prop type for a `true`/`false` pair is
 * an inference detail rather than a documented contract. Strings stay
 * predictable, and full-width buttons are one `className` away.
 */
export const buttonVariants = cva(
  // Focus styling is global (`:focus-visible` in globals.css) so it applies to
  // every control, including ones added later.
  'inline-flex items-center justify-center gap-2 rounded-md border text-sm font-medium transition-colors motion-reduce:transition-none disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        primary:
          'border-transparent bg-content text-surface hover:bg-content/90 active:bg-content/80',
        secondary:
          'border-edge bg-surface-raised text-content hover:bg-surface-sunken active:bg-edge',
        ghost:
          'border-transparent bg-transparent text-content-muted hover:bg-surface-sunken hover:text-content',
        danger: 'border-verdict-fail/40 bg-verdict-fail/10 text-verdict-fail hover:bg-verdict-fail/20',
      },
      size: {
        sm: 'h-7 px-2.5 text-xs',
        md: 'h-9 px-3.5',
        lg: 'h-11 px-5',
      },
    },
    defaultVariants: {
      variant: 'secondary',
      size: 'md',
    },
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
      // Defaulting to `button` matters: a bare `<button>` inside a form submits
      // it, and the sign-up form has buttons that must not.
      type={type ?? 'button'}
      className={cn(buttonVariants({ variant, size }), className)}
      {...rest}
    />
  );
}
