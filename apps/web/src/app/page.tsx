/**
 * Landing page.
 *
 * A gateway rather than a demo. Showing a non-functional editor here would imply
 * a capability that does not exist, and an assessment tool that opens on a
 * marketing page is a marketing page rather than a tool.
 */
import Link from 'next/link';
import { ArrowRight, Gauge, ListChecks, ShieldCheck } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import { buttonVariants } from '@/components/ui/Button';
import { Card, CardContent } from '@/components/ui/Card';
import { cn } from '@/lib/cn';

interface Destination {
  readonly href: string;
  readonly title: string;
  readonly detail: string;
  readonly icon: LucideIcon;
}

const DESTINATIONS: readonly Destination[] = [
  {
    href: '/challenges',
    title: 'Challenges',
    detail:
      'Practical tasks graded only by tests you cannot read. Run to iterate, submit to record the attempt.',
    icon: ListChecks,
  },
  {
    href: '/dashboard',
    title: 'Skill matrix',
    detail:
      'What the model believes you have demonstrated across the concept graph, and where the evidence is thin.',
    icon: Gauge,
  },
];

export default function Home(): JSX.Element {
  return (
    {/*
      A `div`, not a `main`: the root layout already owns the `main` landmark, and
      nesting a second one is invalid and gives assistive technology two competing
      document outlines.
    */}
    <div className="mx-auto flex min-h-screen max-w-3xl flex-col justify-center gap-8 px-6 py-16">
      <header className="space-y-3">
        <div className="inline-flex items-center gap-2 rounded-full border border-edge px-3 py-1 text-xs text-content-muted">
          <ShieldCheck aria-hidden className="size-3.5" />
          Every submission treated as hostile
        </div>
        <h1 className="text-3xl font-semibold tracking-tight">Adaptive Assessment Simulator</h1>
        <p className="text-content-muted">
          Practical engineering tasks, graded only by hidden unit tests, with a probabilistic model
          of your mastery driving what comes next.
        </p>
      </header>

      <nav aria-label="Destinations" className="grid gap-3 sm:grid-cols-2">
        {DESTINATIONS.map((destination) => {
          const Icon = destination.icon;
          return (
            <Card
              key={destination.href}
              className="transition-colors motion-reduce:transition-none hover:border-content-subtle"
            >
              <CardContent className="flex h-full flex-col gap-2">
                <div className="flex items-center gap-2">
                  <Icon aria-hidden className="size-4 text-content-subtle" />
                  <h2 className="text-sm font-semibold">{destination.title}</h2>
                </div>
                <p className="flex-1 text-sm text-content-muted">{destination.detail}</p>
                <Link
                  href={destination.href}
                  className={cn(buttonVariants({ variant: 'secondary', size: 'sm' }), 'w-fit')}
                >
                  Open {destination.title.toLowerCase()}
                  <ArrowRight aria-hidden className="size-3.5" />
                </Link>
              </CardContent>
            </Card>
          );
        })}
      </nav>

      <footer className="space-y-1 text-xs text-content-subtle">
        <p>
          API docs at <code className="font-mono">/docs</code> · Prometheus at{' '}
          <code className="font-mono">/metrics</code>
        </p>
      </footer>
    </div>
  );
}
