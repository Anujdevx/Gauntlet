'use client';

/**
 * Per-test verdicts, score summary, live run progress and the platform-fault panel.
 *
 * The panel's most important job is refusing to lie about causality. When the
 * runner reports `infra_error` the tests did not evaluate the candidate's code,
 * so rendering a red score would tell them their solution is wrong for a reason
 * that has nothing to do with it. That case gets its own panel, states plainly
 * that it is a platform fault, and shows no score at all. `error` gets the same
 * treatment with different wording, because `countsAgainstCandidate` is false
 * for it too.
 *
 * Everything decorative (staggered row entrance, confetti) is CSS-driven and
 * disabled by the global reduced-motion rule.
 */
import { useState } from 'react';
import Link from 'next/link';
import { ArrowRight, Check, ChevronRight, CircleAlert, CircleCheck, CircleSlash, Play, Send, ServerCrash } from 'lucide-react';

import { cn } from '@/lib/cn';
import { SUBMIT_EVENT } from '@/lib/shortcuts';
import {
  formatDuration,
  sanitizeSingleLine,
  statusChipClass,
} from '@/lib/format';
import { countsAgainstCandidate, STATUS_LABELS } from '@/types/execution';
import type { ExecutionResult, ExecutionStatus, TestCaseResult } from '@/types/execution';
import { useIdeStore } from '@/store/ide';
import type { ConnectionState } from '@/store/ide';
import { ProgressRing } from '@/components/ui/ProgressRing';

/** Copy for a status that is not evidence about the candidate. */
const PLATFORM_FAULT_COPY: Readonly<Record<'infra_error' | 'error', string>> = {
  infra_error:
    'The assessment infrastructure failed while running this submission. Your code was not the cause and nothing about your solution can be concluded from this run. No result has been recorded.',
  error:
    'The test harness could not be executed, so no test evaluated your code. This is not a test failure. No result has been recorded.',
};

/** Derived from `countsAgainstCandidate` so a new status cannot be silently missed. */
function isPlatformFault(status: ExecutionStatus): boolean {
  return !countsAgainstCandidate(status);
}

export function TestResults(): JSX.Element {
  const result = useIdeStore((state) => state.result);
  const connection = useIdeStore((state) => state.connection);

  if (result === null) {
    return <PendingState connection={connection} />;
  }

  if (isPlatformFault(result.status)) {
    return <PlatformFaultPanel result={result} />;
  }

  // A pass with zero tests means nothing was graded. `run` mode does this by design
  // (no hidden tests are present while candidate code executes); anything else is an
  // anomaly. Either way it must never be shown as a green "all tests passed".
  if (result.status === 'passed' && result.score.total === 0) {
    return <NoVerdictPanel result={result} />;
  }

  return (
    <div className="scrollbar-thin relative size-full overflow-auto" role="region" aria-label="Test results">
      {result.status === 'passed' ? <Confetti key={result.submission_id} /> : null}
      <ScoreHeader result={result} />
      <TestTable tests={result.tests} />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Pending / live progress                                                     */
/* -------------------------------------------------------------------------- */

const PIPELINE = ['Queued', 'Sandbox started', 'Running hidden tests'] as const;

function PendingState({ connection }: { readonly connection: ConnectionState }): JSX.Element {
  const busy = connection === 'connecting' || connection === 'streaming' || connection === 'polling';

  if (!busy) {
    const message =
      connection === 'error'
        ? 'The run ended without a result.'
        : connection === 'done'
          ? 'The run finished but returned no result.'
          : null;
    return (
      <div className="flex size-full flex-col items-center justify-center gap-2 px-6 text-center">
        {message === null ? (
          <>
            <span className="mb-1 inline-flex size-10 items-center justify-center rounded-full bg-accent/10 text-accent ring-1 ring-accent/25">
              <ChevronRight aria-hidden className="size-5" />
            </span>
            <p className="text-sm font-medium">No run yet</p>
            <p className="max-w-xs text-xs text-content-subtle">
              Press <strong className="font-medium text-content-muted">Run</strong> to execute your
              code, or <strong className="font-medium text-content-muted">Submit</strong> to grade it.
            </p>
          </>
        ) : (
          <p className="text-sm text-content-muted">{message}</p>
        )}
      </div>
    );
  }

  const active = connection === 'connecting' ? 0 : 2;
  return (
    <div className="flex size-full items-center justify-center px-6" role="status" aria-live="polite">
      <ol className="space-y-3">
        {PIPELINE.map((label, index) => {
          const done = index < active;
          const current = index === active;
          return (
            <li key={label} className="flex items-center gap-3 text-sm">
              <span
                className={cn(
                  'inline-flex size-5 items-center justify-center rounded-full border text-[10px] transition-colors',
                  done && 'border-verdict-pass bg-verdict-pass/15 text-verdict-pass',
                  current && 'animate-pulse-ring border-accent bg-accent/15 text-accent',
                  !done && !current && 'border-edge-strong text-content-subtle',
                )}
              >
                {done ? <Check aria-hidden className="size-3" /> : index + 1}
              </span>
              <span className={cn(current ? 'font-medium text-content' : done ? 'text-content-muted' : 'text-content-subtle')}>
                {label}
                {current ? '…' : ''}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Executed, but nothing graded                                                */
/* -------------------------------------------------------------------------- */

function NoVerdictPanel({ result }: { readonly result: ExecutionResult }): JSX.Element {
  const mode = useIdeStore((state) => state.mode);
  const isRun = mode === 'run';
  return (
    <div className="scrollbar-thin size-full overflow-auto" role="region" aria-label="Run finished">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-3 px-4 py-4">
        <span
          className={cn(
            'inline-flex size-12 shrink-0 items-center justify-center rounded-full ring-1',
            isRun ? 'bg-accent/10 text-accent ring-accent/30' : 'bg-verdict-skip/10 text-verdict-skip ring-verdict-skip/30',
          )}
        >
          {isRun ? <Play aria-hidden className="size-5" /> : <CircleAlert aria-hidden className="size-5" />}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-base font-semibold">
            {isRun ? 'Your code ran' : 'The grader returned no test results'}
          </p>
          <p className="mt-0.5 max-w-xl text-xs leading-relaxed text-content-subtle">
            {isRun
              ? 'Run executes your code and shows what it prints or raises, but it does not grade. The hidden tests only exist at submit time. Check the Output tab, then Submit to be graded.'
              : 'Nothing was evaluated, so this is not a pass. Try submitting again.'}
          </p>
        </div>
        {isRun ? (
          <button
            type="button"
            onClick={() => window.dispatchEvent(new Event(SUBMIT_EVENT))}
            className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg shadow-glow-sm transition-all hover:brightness-110"
          >
            <Send aria-hidden className="size-3.5" />
            Submit for grading
          </button>
        ) : null}
      </div>
      <p className="px-4 pb-4 font-mono text-xs text-content-subtle">
        finished in {formatDuration(result.duration_ms_total ?? result.runtime_ms)}
      </p>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Celebration                                                                 */
/* -------------------------------------------------------------------------- */

const CONFETTI_COLORS = ['rgb(var(--accent))', 'rgb(var(--verdict-pass))', 'rgb(var(--verdict-skip))', '#7aa2f7'];

/** Deterministic pseudo-random in [0,1) so the burst is stable across renders. */
function rand(seed: number): number {
  const x = Math.sin(seed * 9301 + 49297) * 233280;
  return x - Math.floor(x);
}

function Confetti(): JSX.Element {
  return (
    <div aria-hidden className="pointer-events-none absolute left-14 top-6 z-10">
      {Array.from({ length: 28 }, (_, i) => {
        const angle = (i / 28) * Math.PI * 2 + rand(i) * 0.5;
        const distance = 70 + rand(i + 50) * 110;
        return (
          <span
            key={i}
            className="absolute size-1.5 animate-confetti rounded-[1px]"
            style={
              {
                backgroundColor: CONFETTI_COLORS[i % CONFETTI_COLORS.length],
                '--dx': `${Math.cos(angle) * distance}px`,
                '--dy': `${Math.sin(angle) * distance - 20}px`,
                '--rot': `${(rand(i + 9) - 0.5) * 720}deg`,
                animationDelay: `${rand(i + 3) * 120}ms`,
              } as React.CSSProperties
            }
          />
        );
      })}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Platform fault                                                              */
/* -------------------------------------------------------------------------- */

function PlatformFaultPanel({ result }: { readonly result: ExecutionResult }): JSX.Element {
  const copy: string = PLATFORM_FAULT_COPY[result.status as 'infra_error' | 'error'];
  const language = result.language ?? 'the runner';
  return (
    <div className="scrollbar-thin size-full overflow-auto" role="region" aria-label="Platform error">
      <div className="m-4 rounded-panel border border-verdict-infra/40 bg-verdict-infra/10 p-4">
        <div className="flex items-start gap-3">
          <ServerCrash aria-hidden className="mt-0.5 size-5 shrink-0 text-verdict-infra" />
          <div className="min-w-0 space-y-2">
            <h3 className="text-sm font-semibold text-verdict-infra">
              Platform error — this is not your code
            </h3>
            <p className="text-sm text-content-muted">{copy}</p>
            <p className="text-xs text-content-subtle">
              Try running again. If it keeps happening, the attempt has not been recorded against
              your skill profile, so it will not affect your mastery.
            </p>

            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 pt-1 text-xs text-content-subtle">
              <dt>Reported status</dt>
              <dd className="font-mono">{result.status}</dd>
              <dt>Language</dt>
              <dd className="font-mono">{sanitizeSingleLine(language, 40)}</dd>
              <dt>Exit code</dt>
              <dd className="font-mono">{result.exit_code === null ? 'not reported' : result.exit_code}</dd>
              <dt>Runtime</dt>
              <dd className="font-mono">{formatDuration(result.runtime_ms)}</dd>
              <dt>Recorded against you</dt>
              <dd>{result.counts_against_candidate ? 'yes' : 'no'}</dd>
            </dl>
          </div>
        </div>
      </div>

      {/* The runner's own output often explains the fault; hiding it would send the
          candidate hunting for an error in their own code. */}
      {result.stderr !== '' ? (
        <div className="px-4 pb-4">
          <PreBlock label="Runner output (stderr)" value={result.stderr} />
        </div>
      ) : null}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Score                                                                       */
/* -------------------------------------------------------------------------- */

function ScoreHeader({ result }: { readonly result: ExecutionResult }): JSX.Element {
  const { score } = result;
  const mode = useIdeStore((state) => state.mode);
  const passed = result.status === 'passed';

  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-3 border-b border-edge px-4 py-3">
      <ProgressRing
        value={score.ratio}
        size={56}
        stroke={5}
        label="Tests passed"
        arcClassName={passed ? 'stroke-verdict-pass' : 'stroke-verdict-fail'}
      >
        <span className="font-mono text-xs font-semibold tabular-nums">
          {Math.round(score.percentage)}%
        </span>
      </ProgressRing>

      <div className="min-w-0">
        <p className={cn('text-base font-semibold', passed ? 'text-verdict-pass' : 'text-content')}>
          {STATUS_LABELS[result.status]}
        </p>
        <p className="text-xs text-content-subtle tabular-nums">
          {score.passed}/{score.total} tests ·{' '}
          <span className="font-mono">{formatDuration(result.runtime_ms)}</span>
        </p>
      </div>

      <span
        className={cn(
          'rounded-full border px-2.5 py-0.5 text-[11px] font-medium',
          statusChipClass(result.status),
        )}
      >
        {mode === 'submit' ? 'Recorded' : 'Practice run'}
      </span>

      {mode === 'submit' && passed ? (
        <Link
          href="/dashboard"
          className="ml-auto inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg shadow-glow-sm transition-all hover:brightness-110"
        >
          View skill map
          <ArrowRight aria-hidden className="size-3.5" />
        </Link>
      ) : null}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Test rows                                                                   */
/* -------------------------------------------------------------------------- */

function TestTable({ tests }: { readonly tests: readonly TestCaseResult[] }): JSX.Element {
  if (tests.length === 0) {
    return (
      <p className="px-4 py-4 text-xs text-content-subtle">
        The runner reported no individual test results.
      </p>
    );
  }
  // Open the first failure by default: it is almost always the one to read.
  const firstFailure = tests.findIndex((test) => !test.passed && !test.skipped);
  return (
    <ul className="divide-y divide-edge/60">
      {tests.map((test, index) => (
        <TestRow key={test.id} test={test} index={index} defaultExpanded={index === firstFailure} />
      ))}
    </ul>
  );
}

function TestRow({
  test,
  index,
  defaultExpanded,
}: {
  readonly test: TestCaseResult;
  readonly index: number;
  readonly defaultExpanded: boolean;
}): JSX.Element {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const regionId = `test-detail-${test.id}`;

  // A skipped test is neither passed nor failed: counting it either way would
  // inflate the score or penalise code that never had the chance to run.
  const verdict = test.skipped ? 'skipped' : test.passed ? 'passed' : 'failed';
  const Icon = test.skipped ? CircleSlash : test.passed ? CircleCheck : CircleAlert;
  const iconClass = test.skipped
    ? 'text-verdict-skip'
    : test.passed
      ? 'text-verdict-pass'
      : 'text-verdict-fail';

  return (
    <li className="animate-slide-in" style={{ animationDelay: `${Math.min(index, 12) * 45}ms` }}>
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={regionId}
        onClick={() => setExpanded((current) => !current)}
        className={cn(
          'flex w-full items-center gap-2.5 px-4 py-2.5 text-left text-sm transition-colors motion-reduce:transition-none hover:bg-surface-overlay',
          !test.passed && !test.skipped && 'bg-verdict-fail/[0.04]',
        )}
      >
        <ChevronRight
          aria-hidden
          className={cn(
            'size-3.5 shrink-0 text-content-subtle transition-transform motion-reduce:transition-none',
            expanded && 'rotate-90',
          )}
        />
        <Icon aria-hidden className={cn('size-4 shrink-0', iconClass)} />
        {/* The icon is decorative; without this a screen reader announces the test
            name with no indication of whether it passed. */}
        <span className="sr-only">{verdict}:</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[13px]">
          {sanitizeSingleLine(test.name, 160)}
        </span>
        <span className="shrink-0 font-mono text-xs tabular-nums text-content-subtle">
          {formatDuration(test.duration_ms)}
        </span>
      </button>

      <div id={regionId} hidden={!expanded} className="space-y-2 px-4 pb-3 pl-14">
        {test.message !== null && test.message !== '' ? (
          <PreBlock label="Message" value={test.message} />
        ) : null}
        {test.stdout !== null && test.stdout !== '' ? <PreBlock label="stdout" value={test.stdout} /> : null}
        {test.stderr !== null && test.stderr !== '' ? <PreBlock label="stderr" value={test.stderr} /> : null}
        {test.stack_trace !== null && test.stack_trace !== '' ? (
          <PreBlock label="Stack trace" value={test.stack_trace} />
        ) : null}
        {test.message === null &&
        test.stdout === null &&
        test.stderr === null &&
        test.stack_trace === null ? (
          <p className="text-xs text-content-subtle">
            Hidden tests do not reveal their expected output. The test name describes what it checks.
          </p>
        ) : null}
      </div>
    </li>
  );
}

/** Untrusted multi-line text. Rendered as text content, never as markup. */
function PreBlock({ label, value }: { readonly label: string; readonly value: string }): JSX.Element {
  return (
    <div>
      <p className="mb-1 text-[10px] font-medium uppercase tracking-wider text-content-subtle">{label}</p>
      <pre className="scrollbar-thin max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-edge bg-surface-sunken p-3 font-mono text-xs leading-relaxed text-content-muted">
        {value}
      </pre>
    </div>
  );
}
