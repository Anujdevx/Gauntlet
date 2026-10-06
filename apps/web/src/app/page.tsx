/**
 * Landing page.
 *
 * Opens on the product itself: an animated run of the real experience (code,
 * hidden tests, score) rather than marketing copy, then the three ideas that
 * make Gauntlet different, then the loop it runs.
 */
import Link from 'next/link';
import { ArrowRight, BrainCircuit, EyeOff, ShieldCheck } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import { Logo } from '@/components/brand/Logo';
import { HeroDemo } from '@/components/landing/HeroDemo';
import { AppShell } from '@/components/layout/AppShell';
import { buttonVariants } from '@/components/ui/Button';
import { cn } from '@/lib/cn';

interface Pillar {
  readonly title: string;
  readonly detail: string;
  readonly icon: LucideIcon;
}

const PILLARS: readonly Pillar[] = [
  {
    title: 'Hidden tests decide. Never an AI.',
    detail:
      'Every verdict is a pass or fail from unit tests you cannot read. No vibes, no rubric, no LLM grading your code. The same solution always scores the same.',
    icon: EyeOff,
  },
  {
    title: 'Run like the code is hostile.',
    detail:
      'Each submission gets its own single-use container: no network, read-only filesystem, dropped capabilities, hard time and memory limits, behind gVisor.',
    icon: ShieldCheck,
  },
  {
    title: 'It learns your weak spots.',
    detail:
      'A Bayesian model tracks mastery for every skill in the syllabus. The next question comes from whatever you have actually proven you are weakest at.',
    icon: BrainCircuit,
  },
];

const STEPS: readonly { readonly title: string; readonly detail: string }[] = [
  { title: 'Pick a task', detail: 'Real engineering work in React, SQL and Python, not abstract puzzles.' },
  { title: 'Write and run', detail: 'A full Monaco editor. Results stream back in about two seconds.' },
  { title: 'Get graded', detail: 'Hidden tests give a deterministic per-test verdict.' },
  { title: 'See the gaps', detail: 'Every attempt is recorded against the skills it exercises.' },
];

export default function Home(): JSX.Element {
  return (
    <AppShell>
      <div className="relative overflow-hidden">
        <div aria-hidden className="bg-grid absolute inset-x-0 top-0 h-[34rem]" />
        <div
          aria-hidden
          className="absolute left-1/2 top-[-12rem] h-[28rem] w-[56rem] -translate-x-1/2 rounded-full bg-accent/15 blur-[120px]"
        />

        <section className="relative mx-auto max-w-5xl px-6 pb-16 pt-20 text-center sm:pt-28">
          <div className="mx-auto mb-7 inline-flex animate-fade-up items-center gap-2 rounded-full border border-edge-strong bg-surface-raised/80 px-3.5 py-1.5 text-xs text-content-muted">
            <span className="relative flex size-2">
              <span className="absolute inline-flex size-full animate-pulse-ring rounded-full" />
              <span className="relative inline-flex size-2 rounded-full bg-accent" />
            </span>
            Zero-trust sandbox · Deterministic grading · Adaptive practice
          </div>

          <h1
            className="animate-fade-up text-balance text-4xl font-semibold tracking-tight sm:text-6xl"
            style={{ animationDelay: '60ms' }}
          >
            Practice the way
            <br />
            you&rsquo;ll be <span className="text-gradient animate-shimmer">tested.</span>
          </h1>

          <p
            className="mx-auto mt-6 max-w-2xl animate-fade-up text-balance text-base leading-relaxed text-content-muted sm:text-lg"
            style={{ animationDelay: '120ms' }}
          >
            Gauntlet turns a job description into a graded, adaptive engineering assessment.
            Write real code in a real editor, get judged by hidden unit tests, and see exactly
            which skills you have proven.
          </p>

          <div
            className="mt-9 flex animate-fade-up flex-wrap items-center justify-center gap-3"
            style={{ animationDelay: '180ms' }}
          >
            <Link href="/challenges" className={buttonVariants({ variant: 'primary', size: 'lg' })}>
              Enter the gauntlet
              <ArrowRight aria-hidden className="size-4" />
            </Link>
            <Link href="/dashboard" className={buttonVariants({ variant: 'secondary', size: 'lg' })}>
              View my skills
            </Link>
          </div>

          <div className="mt-16 animate-fade-up" style={{ animationDelay: '260ms' }}>
            <HeroDemo />
          </div>
        </section>

        <section aria-labelledby="pillars" className="relative mx-auto max-w-6xl px-6 py-16">
          <h2 id="pillars" className="sr-only">
            What makes Gauntlet different
          </h2>
          <ul className="grid gap-4 md:grid-cols-3">
            {PILLARS.map((pillar) => {
              const Icon = pillar.icon;
              return (
                <li
                  key={pillar.title}
                  className="card-lift rounded-panel border border-edge bg-surface-raised p-6"
                >
                  <span className="mb-4 inline-flex size-10 items-center justify-center rounded-lg bg-accent/10 text-accent ring-1 ring-accent/25">
                    <Icon aria-hidden className="size-5" />
                  </span>
                  <h3 className="text-[15px] font-semibold tracking-tight">{pillar.title}</h3>
                  <p className="mt-2 text-sm leading-relaxed text-content-muted">{pillar.detail}</p>
                </li>
              );
            })}
          </ul>
        </section>

        <section aria-labelledby="loop" className="relative mx-auto max-w-5xl px-6 py-16">
          <h2 id="loop" className="text-center text-2xl font-semibold tracking-tight sm:text-3xl">
            One loop. Every attempt counts.
          </h2>
          <ol className="mt-12 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            {STEPS.map((step, index) => (
              <li key={step.title} className="relative">
                <span className="font-mono text-xs text-accent">0{index + 1}</span>
                <h3 className="mt-1 text-base font-semibold">{step.title}</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-content-muted">{step.detail}</p>
                {index < STEPS.length - 1 ? (
                  <span
                    aria-hidden
                    className="absolute -right-3 top-2 hidden h-px w-6 bg-gradient-to-r from-edge-strong to-transparent lg:block"
                  />
                ) : null}
              </li>
            ))}
          </ol>
        </section>

        <section className="relative mx-auto max-w-4xl px-6 pb-24 pt-8">
          <div className="relative overflow-hidden rounded-panel border border-accent/30 bg-surface-raised px-8 py-14 text-center">
            <div
              aria-hidden
              className="absolute inset-0 bg-gradient-to-br from-accent/15 via-transparent to-transparent"
            />
            <h2 className="relative text-2xl font-semibold tracking-tight sm:text-3xl">
              Ready when you are.
            </h2>
            <p className="relative mx-auto mt-3 max-w-md text-sm text-content-muted">
              The first challenge takes about ten minutes. The honest picture of your skills
              lasts a lot longer.
            </p>
            <Link
              href="/login"
              className={cn(buttonVariants({ variant: 'primary', size: 'lg' }), 'relative mt-7')}
            >
              Create your account
              <ArrowRight aria-hidden className="size-4" />
            </Link>
          </div>
        </section>

        <footer className="relative border-t border-edge">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-6 py-6 text-xs text-content-subtle">
            <Logo className="opacity-80" />
            <p>
              API docs at <code className="font-mono">/docs</code> · metrics at{' '}
              <code className="font-mono">/metrics</code>
            </p>
          </div>
        </footer>
      </div>
    </AppShell>
  );
}
