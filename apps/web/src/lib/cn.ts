/**
 * Conditional class name composition.
 *
 * Exists as a single function rather than being inlined at each call site
 * because two things have to happen together that are easy to get wrong
 * separately: conditional joining (`clsx`) and Tailwind conflict resolution
 * (`tailwind-merge`). Passing `cn('p-2', 'p-4')` must yield `p-4`; a plain
 * template literal emits both classes and lets CSS specificity decide, which
 * makes the winner depend on stylesheet order rather than on the call site.
 */
import { clsx } from 'clsx';
import type { ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/** Compose conditional classes and resolve Tailwind conflicts left-to-right. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
