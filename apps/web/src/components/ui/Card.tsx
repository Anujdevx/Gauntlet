/**
 * Surface container, split into named sections so panel spacing stays consistent.
 */
import type { HTMLAttributes, ReactNode } from 'react';

import { cn } from '@/lib/cn';

export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>): JSX.Element {
  return (
    <div
      className={cn('rounded-panel border border-edge bg-surface-raised text-content', className)}
      {...rest}
    />
  );
}

interface CardSectionProps extends HTMLAttributes<HTMLDivElement> {
  readonly children: ReactNode;
}

export function CardHeader({ className, children, ...rest }: CardSectionProps): JSX.Element {
  return (
    <div className={cn('flex flex-col gap-1 border-b border-edge px-5 py-4', className)} {...rest}>
      {children}
    </div>
  );
}

interface CardHeadingProps extends HTMLAttributes<HTMLHeadingElement> {
  readonly children: ReactNode;
}

export function CardTitle({ className, children, ...rest }: CardHeadingProps): JSX.Element {
  return (
    <h2 className={cn('text-sm font-semibold tracking-tight', className)} {...rest}>
      {children}
    </h2>
  );
}

export function CardDescription({ className, children, ...rest }: CardSectionProps): JSX.Element {
  return (
    <p className={cn('text-xs text-content-muted', className)} {...rest}>
      {children}
    </p>
  );
}

export function CardContent({ className, children, ...rest }: CardSectionProps): JSX.Element {
  return (
    <div className={cn('px-5 py-4', className)} {...rest}>
      {children}
    </div>
  );
}

export function CardFooter({ className, children, ...rest }: CardSectionProps): JSX.Element {
  return (
    <div
      className={cn('flex items-center gap-2 border-t border-edge px-5 py-4', className)}
      {...rest}
    >
      {children}
    </div>
  );
}
