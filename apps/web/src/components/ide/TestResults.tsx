'use client';

/**
 * Per-test verdicts, score summary, and the platform-fault panel.
 *
 * The panel's most important job is refusing to lie about causality. When the
 * runner reports `infra_error` the tests did not evaluate the candidate's code,
 * so rendering a red score would tell them their solution is wrong for a reason
 * that has nothing to do with it. That case gets its own panel, states plainly
 * that it is a platform fault, and shows no score at all. `error` gets the same
 * treatment with different wording, because `countsAgainstCandidate` is false
 * for it too.
 */
import { useState } from 'react';
import { ChevronRight, CircleAlert, CircleCheck, CircleSlash, ServerCrash } from 'lucide-react';

import { cn } from '@/lib/cn';
import {
  formatDuration,
  formatPercentPoints,
  sanitizeSingleLine,
  statusChipClass,
  statusTextClass,
} from '@/lib/format';
import { countsAgainstCandidate, STATUS_LABELS } from '@/types/execution';
import type { ExecutionResult, ExecutionStatus, TestCaseResult } from '@/types/execution';
import { useIdeStore } from '@/store/ide';
import { Spinner } from '@/components/ui/Spinner';
import type { ConnectionState } from '@/store/ide';

/**
 * Copy for a status that is not evidence about the candidate.
 *
 * `infra_error` and `error` are separated because they mean different things:
 * one is our infrastructure, the other is the harness failing to execute at all.
 * Both must be distinguished from a candidate failure, for the same reason.
 */
const PLATFORM_FAULT_COPY: Readonly<Record<'infra_error' | 'error', string>> = {
  infra_error:
    'The assessment infrastructure failed while running this submission. Your code was not the cause and nothing about your solution can be concluded from this run. No result has been recorded.',
  error:
    'The test harness could not be executed, so no test evaluated your code. This is not a test failure. No result has been recorded.',
};

/**
 * Statuses for which the panel must not present a score.
 *
 * Derived from `countsAgainstCandidate` rather than duplicated as a literal list,
 * so a status added to `execution.ts` cannot be silently forgotten here. The cast
 * to the narrower key is what makes the copy lookup total.
 */
function isPlatformFault(status: ExecutionStatus): boolean {
  return !countsAgainstCandidate(status);
}

export function TestResults(): JSX.Element {
  const result = useIdeStore((state) => state.result);
  const connection = useIdeStore((state) => state.connection);
  const liveStatus = useIdeStore((state) => state.liveStatus);

  if (result === null) {
    return <PendingState connection={connection} liveStatus={liveStatus} />;
  }

  if (isPlatformFault(result.status)) {
    return <PlatformFaultPanel result={result} />;
  }

  return (
    <div className="scrollbar-thin size-full overflow-auto" role="region" aria-label="Test results">
      <ScoreHeader result={result} />
      <TestTable tests={result.tests} />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Loading and no-result states                                                */
/* -------------------------------------------------------------------------- */

interface PendingStateProps {
  readonly connection: ConnectionState;
  readonly liveStatus: ExecutionStatus | null;
}

const PENDING_LABELS: Readonly<Record<ConnectionState, string>> = {
  idle: 'No run yet. Use Run to check your solution against the visible checks.',
  connecting: 'Queueing the run…',
  streaming: 'Running…',
  polling: 'Waiting for the result…',
  done: 'The run finished but returned no result.',
  error: 'The run ended without a result.',
};

function PendingState({ connection, liveStatus }: PendingStateProps): JSX.Element {
  const busy = connection === 'connecting' || connection === 'streaming' || connection === 'polling';
  return (
    <div className="flex size-full flex-col items-center justify-center gap-2 px-6 text-center">
      {busy ? <Spinner /> : null}
      <p className="text-sm text-content-muted">{PENDING_LABELS[connection]}</p>
      {liveStatus !== null && busy ? (
        <p className="text-xs text-content-subtle">Last status: {STATUS_LABELS[liveStatus]}</p>
      ) : null}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Platform fault                                                              */
/* -------------------------------------------------------------------------- */

interface PlatformFaultPanelProps {
  readonly result: ExecutionResult;
}

function PlatformFaultPanel({ result }: PlatformFaultPanelProps): JSX.Element {
  // `isPlatformFault` guarantees membership, so the lookup is total here. The
  // fallback exists only to satisfy the type checker, not to be reachable.
  const copy: string = PLATFORM_FAULT_COPY[result.status as 'infra_error' | 'error'];
  const language = result.language ?? 'the runner';
  return (
    <div
      className="scrollbar-thin size-full overflow-auto"
      role="region"
      aria-label="Platform error"
    >
      <div className="m-3 rounded-panel border border-verdict-infra/40 bg-verdict-infra/10 p-4">
        <div className="flex items-start gap-3">
          <ServerCrash aria-hidden className="mt-0.5 size-5 shrink-0 text-verdict-infra" />
          <div className="min-w-0 space-y-2">
            <h3 className="text-sm font-semibold text-verdict-infra">
              Platform error — this is not your code
            </h3>
            <p className="text-sm text-content-muted">{copy}</p>            <p className="text-xs text-content-subtle">
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

      {/*
        The runner's own output is still shown, but under neutral styling. It
        frequently explains the platform fault, and hiding it would send the
        candidate hunting for an error in their own code.
      */}
      {result.stderr !== '' ? <PreBlock label="Runner output (stderr)" value={result.stderr} /> : null}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Score                                                                       */
/* -------------------------------------------------------------------------- */

function ScoreHeader({ result }: { readonly result: ExecutionResult }): JSX.Element {
  const { score } = result;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-edge px-3 py-2.5">
      <p className={cn('text-lg font-semibold tabular-nums', statusTextClass(result.status))}>
        {formatPercentPoints(score.percentage)}
        <span className="text-sm font-normal text-content-subtle">
          {' '}
          ({score.passed}/{score.total})
        </span>
      </p>
      <span
        className={cn(
          'rounded-full border px-2 py-0.5 text-xs font-medium',
          statusChipClass(result.status),
        )}
      >
        {STATUS_LABELS[result.status]}
      </span>
      <dl className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-content-subtle">
        <div className="flex gap-1">
          <dt>Passed</dt>
          <dd className="font-medium text-verdict-pass">{score.passed}</dd>
        </div>
        <div className="flex gap-1">
          <dt>Failed</dt>
          <dd className="font-medium text-verdict-fail">{score.failed}</dd>
        </div>
        <div className="flex gap-1">
          <dt>Skipped</dt>
          <dd className="font-medium text-verdict-skip">{score.skipped}</dd>
        </div>
        <div className="flex gap-1">
          <dt>Runtime</dt>
          <dd className="font-mono">{formatDuration(result.runtime_ms)}</dd>
        </div>
      </dl>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Test rows                                                                   */
/* -------------------------------------------------------------------------- */

function TestTable({ tests }: { readonly tests: readonly TestCaseResult[] }): JSX.Element {
  if (tests.length === 0) {
    return (
      <p className="px-3 py-4 text-xs text-content-subtle">
        The runner reported no individual test results.
      </p>
    );
  }
  return (
    <ul className="divide-y divide-edge">
      {tests.map((test) => (
        <TestRow key={test.id} test={test} />
      ))}
    </ul>
  );
}

function TestRow({ test }: { readonly test: TestCaseResult }): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const regionId = `test-detail-${test.id}`;

  /*
   * A skipped test is reported as neither passed nor failed. Counting it as a
   * pass would inflate the score; counting it as a failure would penalise code
   * that never had the chance to run.
   */
  const verdict = test.skipped ? 'skipped' : test.passed ? 'passed' : 'failed';
  const Icon = test.skipped ? CircleSlash : test.passed ? CircleCheck : CircleAlert;
  const iconClass = test.skipped
    ? 'text-verdict-skip'
    : test.passed
      ? 'text-verdict-pass'
      : 'text-verdict-fail';

  return (
    <li>
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={regionId}
        onClick={() => setExpanded((current) => !current)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors motion-reduce:transition-none hover:bg-surface-sunken"
      >
        <ChevronRight
          aria-hidden
          className={cn(
            'size-3.5 shrink-0 text-content-subtle transition-transform motion-reduce:transition-none',
            expanded && 'rotate-90',
          )}
        />
        <Icon aria-hidden className={cn('size-4 shrink-0', iconClass)} />
        {/*
          `sr-only` restatement of the verdict: the icon is decorative, so without
          this a screen reader announces the test name with no indication of
          whether it passed.
        */}
        <span className="sr-only">{verdict}:</span>
        <span className="min-w-0 flex-1 truncate">{sanitizeSingleLine(test.name, 160)}</span>
        <span className="shrink-0 font-mono text-xs text-content-subtle tabular-nums">
          {formatDuration(test.duration_ms)}
        </span>
      </button>

      <div id={regionId} hidden={!expanded} className="space-y-2 px-3 pb-3 pl-9">
        {test.message !== null && test.message !== '' ? (
          <PreBlock label="Message" value={test.message} />
        ) : null}
        {test.stdout !== null && test.stdout !== '' ? (
          <PreBlock label="stdout" value={test.stdout} />
        ) : null}
        {test.stderr !== null && test.stderr !== '' ? (
          <PreBlock label="stderr" value={test.stderr} />
        ) : null}
        {test.stack_trace !== null && test.stack_trace !== '' ? (
          <PreBlock label="Stack trace" value={test.stack_trace} />
        ) : null}
        {test.message === null &&
        test.stdout === null &&
        test.stderr === null &&
        test.stack_trace === null ? (
          <p className="text-xs text-content-subtle">No further detail was reported.</p>
        ) : null}
      </div>
    </li>
  );
}

/** Untrusted multi-line text. Rendered as text content, never as markup. */
function PreBlock({ label, value }: { readonly label: string; readonly value: string }): JSX.Element {
  return (
    <div>
      <p className="mb-0.5 text-xs font-medium uppercase tracking-wide text-content-subtle">
        {label}
      </p>
      <pre className="scrollbar-thin max-h-64 overflow-auto whitespace-pre-wrap break-words rounded border border-edge bg-surface-sunken p-2 font-mono text-xs leading-relaxed text-content-muted">
        {value}
      </pre>
    </div>
  );
}
