'use client';

/**
 * Run output, with stdout and stderr as separate panes.
 *
 * Three behaviours here are correctness requirements, not polish:
 *
 *  - Everything is ANSI-stripped before rendering. Runner output is authored by
 *    the candidate, so it is untrusted text; without stripping, an escape
 *    sequence can repaint the panel or erase the verdict line above it.
 *  - Auto-scroll follows new output only while the candidate is already at the
 *    bottom. Yanking them back down mid-read is the single most common way a
 *    log viewer loses the line someone was looking at, so scrolling up pins the
 *    view and surfaces an explicit jump button instead.
 *  - Truncation is announced. A silently capped buffer would let a candidate
 *    conclude their program ran cleanly because the stack trace that would have
 *    said otherwise had been dropped to protect the browser.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, ArrowDown, Terminal as TerminalIcon } from 'lucide-react';

import { cn } from '@/lib/cn';
import { sanitizeSingleLine, stripAnsi } from '@/lib/format';
import { useIdeStore } from '@/store/ide';
import type { ConnectionState } from '@/store/ide';

/**
 * Distance from the bottom, in pixels, still treated as "at the bottom".
 *
 * A few pixels of slack so sub-pixel layout and a partial last line do not
 * count as the candidate having deliberately scrolled away.
 */
const BOTTOM_SLACK_PX = 24;

const CONNECTION_LABELS: Readonly<Record<ConnectionState, string>> = {
  idle: 'Idle',
  connecting: 'Connecting to runner',
  streaming: 'Streaming',
  polling: 'Waiting for result (polling)',
  done: 'Finished',
  error: 'Stream error',
};

interface OutputPaneProps {
  readonly label: string;
  readonly text: string;
  readonly truncated: boolean;
  readonly emptyHint: string;
}

function OutputPane({ label, text, truncated, emptyHint }: OutputPaneProps): JSX.Element {
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef(true);
  const [showJump, setShowJump] = useState(false);

  /*
   * Stripping is memoised, not done inline: the buffer can be 200 KB and
   * re-running the regex on every unrelated re-render would be the most
   * expensive thing on the page during a streaming run.
   *
   * The store keeps raw bytes so a result payload can be compared against them;
   * sanitising is a presentation concern and stays here.
   */
  const display = useMemo(() => stripAnsi(text), [text]);

  const onScroll = useCallback(() => {
    const viewport = viewportRef.current;
    if (viewport === null) {
      return;
    }
    const distance =
      viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
    const atBottom = distance <= BOTTOM_SLACK_PX;
    stickToBottomRef.current = atBottom;
    setShowJump(!atBottom);
  }, []);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (viewport === null || !stickToBottomRef.current) {
      return;
    }
    // Direct assignment rather than `scrollIntoView` or smooth scrolling: both
    // animate, and both are exactly what `prefers-reduced-motion` forbids.
    viewport.scrollTop = viewport.scrollHeight;
  }, [display]);

  const jumpToBottom = useCallback(() => {
    const viewport = viewportRef.current;
    if (viewport === null) {
      return;
    }
    viewport.scrollTop = viewport.scrollHeight;
    stickToBottomRef.current = true;
    setShowJump(false);
  }, []);

  return (
    <section className="flex min-h-0 flex-1 flex-col" aria-label={label}>
      <div className="flex items-center gap-2 px-4 py-1.5 text-xs text-content-subtle">
        <span className="font-medium uppercase tracking-wide">{label}</span>
        <span aria-hidden>&middot;</span>
        <span>{display === '' ? emptyHint : `${display.length} chars`}</span>
      </div>

      {truncated ? (
        <p
          role="status"
          className="mx-3 mb-1 flex items-start gap-1.5 rounded border border-verdict-skip/40 bg-verdict-skip/10 px-2 py-1 text-xs text-verdict-skip"
        >
          <AlertTriangle aria-hidden className="mt-0.5 size-3.5 shrink-0" />
          <span>
            Output truncated. The earliest part of this stream was dropped to keep the page
            responsive, so the log below is incomplete.
          </span>
        </p>
      ) : null}

      <div className="relative min-h-0 flex-1">
        <div
          ref={viewportRef}
          onScroll={onScroll}
          tabIndex={0}
          className={cn(
            'scrollbar-thin size-full overflow-auto bg-surface-sunken px-4 py-2',
            'font-mono text-xs leading-relaxed text-content-muted',
          )}
        >
          {display === '' ? (
            <p className="text-content-subtle italic">Nothing here yet.</p>
          ) : (
            <pre className="whitespace-pre-wrap break-words">{display}</pre>
          )}
        </div>

        {/*
          A real button rather than a pointer-event overlay: the candidate must be
          able to reach it with a keyboard, which a decorative overlay cannot be.
        */}
        {showJump ? (
          <button
            type="button"
            onClick={jumpToBottom}
            className="absolute bottom-2 right-3 inline-flex items-center gap-1 rounded-full border border-edge bg-surface-raised px-2.5 py-1 text-xs text-content shadow-sm hover:bg-surface-sunken"
          >
            <ArrowDown aria-hidden className="size-3.5" />
            Jump to latest
          </button>
        ) : null}
      </div>
    </section>
  );
}

export function Terminal(): JSX.Element {
  const stdout = useIdeStore((state) => state.stdout);
  const stderr = useIdeStore((state) => state.stderr);
  const stdoutTruncated = useIdeStore((state) => state.stdoutTruncated);
  const stderrTruncated = useIdeStore((state) => state.stderrTruncated);
  const connection = useIdeStore((state) => state.connection);
  const streamError = useIdeStore((state) => state.streamError);
  const streamNotice = useIdeStore((state) => state.streamNotice);

  /*
   * The stderr pane is conditional, unlike stdout.
   *
   * stderr is empty on a healthy run, and a permanently reserved pane would halve
   * the space available for the output that matters. Hiding it means a pane
   * appearing is itself the signal that something went wrong, which is worth the
   * layout shift.
   */
  const hasStderr = stderr !== '';
  const hasAnyOutput = stdout !== '' || hasStderr;

  return (
    <div className="flex size-full min-h-0 flex-col" role="region" aria-label="Run output">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-edge px-4 py-2 text-xs">
        <TerminalIcon aria-hidden className="size-3.5 text-content-subtle" />
        <span
          aria-hidden
          className={
            connection === 'streaming' || connection === 'connecting' || connection === 'polling'
              ? 'size-1.5 animate-pulse rounded-full bg-accent'
              : connection === 'error'
                ? 'size-1.5 rounded-full bg-verdict-fail'
                : connection === 'done'
                  ? 'size-1.5 rounded-full bg-verdict-pass'
                  : 'size-1.5 rounded-full bg-edge-strong'
          }
        />
        <span className="text-content-subtle">{CONNECTION_LABELS[connection]}</span>
        {streamNotice !== null ? (
          <span className="text-verdict-skip">{sanitizeSingleLine(streamNotice, 160)}</span>
        ) : null}
        {streamError !== null ? (
          <span role="alert" className="text-verdict-fail">
            {sanitizeSingleLine(streamError, 200)}
          </span>
        ) : null}
      </header>

      {!hasAnyOutput ? (
        <p className="flex-1 bg-surface-sunken px-4 py-3 font-mono text-xs text-content-subtle">
          <span className="text-accent">$</span> waiting for a run
          <span className="ml-1 inline-block h-3 w-1.5 translate-y-0.5 animate-stream-cursor bg-content-subtle" />
        </p>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">
          <OutputPane
            label="stdout"
            text={stdout}
            truncated={stdoutTruncated}
            emptyHint={hasStderr ? 'No stdout.' : 'empty'}
          />
          {hasStderr ? (
            <OutputPane
              label="stderr"
              text={stderr}
              truncated={stderrTruncated}
              emptyHint="empty"
            />
          ) : null}
        </div>
      )}
    </div>
  );
}
