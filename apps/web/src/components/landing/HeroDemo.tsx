'use client';

/**
 * Looping product demo: code types itself, Run is pressed, hidden tests stream in
 * one by one, and the score ring fills. Under `prefers-reduced-motion` it renders
 * the finished state instead of animating.
 */
import { useEffect, useState } from 'react';
import { CircleCheck, CirclePlay, Loader2 } from 'lucide-react';

import { cn } from '@/lib/cn';
import { LogoMark } from '@/components/brand/Logo';
import { ProgressRing } from '@/components/ui/ProgressRing';

const CODE = `def longest_streak(events):
    best = run = 0
    prev = object()
    for event in events:
        run = run + 1 if event == prev else 1
        best = max(best, run)
        prev = event
    return best
`;

const TESTS: readonly { name: string; ms: number }[] = [
  { name: 'test_empty_list_is_zero', ms: 2 },
  { name: 'test_single_value', ms: 1 },
  { name: 'test_longest_run_in_the_middle', ms: 3 },
  { name: 'test_ties_return_the_length', ms: 2 },
  { name: 'test_one_million_events_is_linear', ms: 118 },
];

type Phase = 'typing' | 'running' | 'done';

/** Tiny hand-rolled highlighter: enough to look like an editor, not a parser. */
function highlight(line: string): JSX.Element[] {
  const parts = line.split(/(\bdef\b|\bfor\b|\bin\b|\breturn\b|\belse\b|\bif\b|\bobject\b|\bmax\b|\d+)/g);
  return parts.map((part, index) => {
    if (/^(def|for|in|return|else|if)$/.test(part)) {
      return (
        <span key={index} className="text-[#c792ea]">
          {part}
        </span>
      );
    }
    if (/^(object|max)$/.test(part)) {
      return (
        <span key={index} className="text-[#82aaff]">
          {part}
        </span>
      );
    }
    if (/^\d+$/.test(part)) {
      return (
        <span key={index} className="text-accent">
          {part}
        </span>
      );
    }
    return <span key={index}>{part}</span>;
  });
}

export function HeroDemo(): JSX.Element {
  const [typed, setTyped] = useState(CODE.length);
  const [phase, setPhase] = useState<Phase>('done');
  const [passed, setPassed] = useState(TESTS.length);

  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    let cancelled = false;
    const timers: ReturnType<typeof setTimeout>[] = [];
    const later = (ms: number, fn: () => void): void => {
      timers.push(
        setTimeout(() => {
          if (!cancelled) fn();
        }, ms),
      );
    };

    const cycle = (): void => {
      setPhase('typing');
      setTyped(0);
      setPassed(0);
      let index = 0;
      const typeNext = (): void => {
        index = Math.min(CODE.length, index + 2);
        setTyped(index);
        if (index < CODE.length) {
          later(22, typeNext);
          return;
        }
        later(500, () => {
          setPhase('running');
          TESTS.forEach((_, testIndex) => later(450 + testIndex * 380, () => setPassed(testIndex + 1)));
          later(450 + TESTS.length * 380 + 300, () => setPhase('done'));
          later(450 + TESTS.length * 380 + 4200, cycle);
        });
      };
      later(400, typeNext);
    };

    cycle();
    return () => {
      cancelled = true;
      timers.forEach(clearTimeout);
    };
  }, []);

  const lines = CODE.slice(0, typed).split('\n');
  const percent = passed / TESTS.length;

  return (
    <div
      aria-hidden
      className="relative mx-auto w-full max-w-4xl animate-float rounded-panel border border-edge-strong bg-surface-sunken shadow-pop"
    >
      <div className="pointer-events-none absolute -inset-px -z-10 rounded-panel bg-gradient-to-b from-accent/30 via-transparent to-transparent blur-2xl" />

      <div className="flex items-center gap-2 border-b border-edge px-4 py-2.5">
        <span className="size-2.5 rounded-full bg-verdict-fail/80" />
        <span className="size-2.5 rounded-full bg-verdict-skip/80" />
        <span className="size-2.5 rounded-full bg-verdict-pass/80" />
        <LogoMark className="ml-3 size-4" />
        <span className="font-mono text-xs text-content-subtle">python-groupby-streaks / solution.py</span>
        <span
          className={cn(
            'ml-auto inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-all',
            phase === 'running'
              ? 'bg-accent/20 text-accent'
              : 'bg-accent text-accent-fg shadow-glow-sm',
          )}
        >
          {phase === 'running' ? (
            <Loader2 className="size-3 animate-spin" />
          ) : (
            <CirclePlay className="size-3" />
          )}
          {phase === 'running' ? 'Running' : 'Run'}
        </span>
      </div>

      <div className="grid md:grid-cols-[1.25fr_1fr]">
        <pre className="min-h-[15rem] overflow-hidden px-4 py-4 text-left font-mono text-[12.5px] leading-6 text-content">
          {lines.map((line, index) => (
            <div key={index} className="flex">
              <span className="mr-4 w-4 select-none text-right text-content-subtle/60">{index + 1}</span>
              <span>
                {highlight(line)}
                {index === lines.length - 1 && phase === 'typing' ? (
                  <span className="ml-px inline-block h-4 w-[7px] translate-y-0.5 animate-stream-cursor bg-accent" />
                ) : null}
              </span>
            </div>
          ))}
        </pre>

        <div className="border-t border-edge p-4 md:border-l md:border-t-0">
          <div className="mb-3 flex items-center gap-3">
            <ProgressRing
              value={percent}
              size={52}
              stroke={5}
              label="Hidden tests passed"
              arcClassName={percent === 1 ? 'stroke-verdict-pass' : 'stroke-accent'}
            >
              <span className="font-mono text-[11px] font-semibold tabular-nums">
                {Math.round(percent * 100)}%
              </span>
            </ProgressRing>
            <div className="text-left">
              <p className="text-sm font-semibold">
                {phase === 'done' ? 'All tests passed' : phase === 'running' ? 'Running hidden tests…' : 'Hidden tests'}
              </p>
              <p className="text-xs text-content-subtle">
                {passed}/{TESTS.length} · graded deterministically
              </p>
            </div>
          </div>
          <ul className="space-y-1">
            {TESTS.map((test, index) => {
              const ok = index < passed;
              return (
                <li
                  key={test.name}
                  className={cn(
                    'flex items-center gap-2 rounded-md px-2 py-1 text-left font-mono text-[11.5px] transition-all duration-300',
                    ok ? 'bg-verdict-pass/10 text-content' : 'text-content-subtle/70',
                  )}
                >
                  {ok ? (
                    <CircleCheck className="size-3.5 shrink-0 text-verdict-pass" />
                  ) : (
                    <span className="size-3.5 shrink-0 rounded-full border border-edge-strong" />
                  )}
                  <span className="min-w-0 flex-1 truncate">{test.name}</span>
                  {ok ? <span className="text-content-subtle">{test.ms}ms</span> : null}
                </li>
              );
            })}
          </ul>
        </div>
      </div>
    </div>
  );
}
