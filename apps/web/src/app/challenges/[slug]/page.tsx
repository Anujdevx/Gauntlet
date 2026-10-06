'use client';

/**
 * The assessment surface: challenge, editor, Run and Submit.
 *
 * This route owns the submission lifecycle rather than delegating it to
 * `IdeLayout`, because the choice between Run and Submit is a domain decision
 * and `mode` is what the backend records as evidence. The layout receives the
 * controls as a node and only decides where they sit.
 *
 * Two rules govern repeated clicks. `startRun` refuses while a run is already in
 * flight, and the stream hook cancels any prior connection before opening a new
 * one, so a candidate who mashes Run ends up with exactly one socket rather than
 * one per press.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { ChevronLeft, CirclePlay, PanelLeft, Send, ServerCrash, Timer } from 'lucide-react';

import { AppShell } from '@/components/layout/AppShell';
import { Button } from '@/components/ui/Button';
import { Card, CardContent } from '@/components/ui/Card';
import { Kbd } from '@/components/ui/Kbd';
import { Spinner } from '@/components/ui/Spinner';
import { IdeLayout } from '@/components/ide/IdeLayout';
import { errorMessage, createSubmission, fetchChallenge, isApiError } from '@/lib/api';
import { DIFFICULTY_META, LANGUAGE_META } from '@/lib/challenge-meta';
import { cn } from '@/lib/cn';
import { formatDuration, sanitizeSingleLine } from '@/lib/format';
import { RUN_EVENT, SUBMIT_EVENT, TOGGLE_SIDEBAR_EVENT } from '@/lib/shortcuts';
import { useExecutionStream } from '@/hooks/useExecutionStream';
import { useResource } from '@/hooks/useResource';
import type { Resource } from '@/hooks/useResource';
import { useIdeStore } from '@/store/ide';
import type { ChallengeDetail, SubmissionMode } from '@/types/api';

/** Ticked once a second; the store keeps the authoritative start time. */
const TIMER_INTERVAL_MS = 1_000;

export default function ChallengePage(): JSX.Element {
  const params = useParams();
  const slug = typeof params.slug === 'string' ? params.slug : '';

  const resource = useResource((signal) => fetchChallenge(slug, signal), [slug]);

  return (
    <AppShell bleed>
      <ChallengeWorkspace resource={resource} />
    </AppShell>
  );
}

interface ChallengeWorkspaceProps {
  readonly resource: Resource<ChallengeDetail>;
}

function ChallengeWorkspace({ resource }: ChallengeWorkspaceProps): JSX.Element {
  const { state, data, error, reload } = resource;
  const challenge = data;

  const stream = useExecutionStream();
  const loadChallengeFiles = useIdeStore((store) => store.loadChallengeFiles);
  const beginRun = useIdeStore((store) => store.beginRun);
  const reset = useIdeStore((store) => store.reset);
  const tickTimer = useIdeStore((store) => store.tickTimer);
  const connection = useIdeStore((store) => store.connection);
  const files = useIdeStore((store) => store.files);
  const mode = useIdeStore((store) => store.mode);

  // Time on task, independent of the per-run timer in the store.
  const sessionStart = useRef(Date.now());
  const [sessionMs, setSessionMs] = useState(0);
  const [draftRestored, setDraftRestored] = useState(false);

  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const busy =
    submitting || connection === 'connecting' || connection === 'streaming' || connection === 'polling';

  /*
   * Load starter files on mount or challenge change, and tear everything down on
   * the way out. The store is a singleton, so without this a candidate who
   * navigates between challenges would find the previous one's buffers, output,
   * and verdict still on screen.
   */
  useEffect(() => {
    if (challenge === null) {
      return;
    }
    loadChallengeFiles(challenge.starter_files);
    const restored = restoreDraft(challenge.slug);
    if (restored) {
      setDraftRestored(true);
    }
    return () => {
      stream.cancel();
      reset();
    };
    // `stream.cancel` and the store actions are stable references, so depending
    // on `stream` here would tear down and rebuild the workspace on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [challenge]);

  /*
   * The elapsed timer is a text update, not an animation loop: it drives no style
   * interpolation and no transforms, so it is unaffected by the reduced-motion
   * preference beyond the global CSS clamp.
   */
  useEffect(() => {
    if (!busy) {
      return;
    }
    const timer = setInterval(() => tickTimer(Date.now()), TIMER_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [busy, tickTimer]);

  const startRun = useCallback(
    async (requestedMode: SubmissionMode) => {
      if (challenge === null) {
        return;
      }
      /*
       * Read the connection state from the store rather than the render-scoped
       * `busy`. `getState()` is always current, so a double click cannot open two
       * sockets; a captured `busy` would still be `false` inside the closure from
       * the first click.
       */
      const store = useIdeStore.getState();
      const inFlight =
        store.connection === 'connecting' ||
        store.connection === 'streaming' ||
        store.connection === 'polling';
      if (inFlight) {
        return;
      }

      setSubmitting(true);
      setSubmitError(null);
      try {
        const payload: Record<string, string> = {};
        for (const file of files) {
          payload[file.path] = file.content;
        }
        const accepted = await createSubmission({
          challenge_id: challenge.id,
          files: payload,
          mode: requestedMode,
        });
        // Order matters: the store must be in its "running" state before the
        // socket opens, or the first frames land in the previous run's buffers.
        beginRun(accepted.submission_id, requestedMode, Date.now());
        stream.start(accepted.submission_id);
      } catch (cause) {
        const offline = isApiError(cause) && cause.isOffline;
        setSubmitError(
          errorMessage(
            cause,
            offline
              ? 'Could not reach the runner. Your code is unchanged; try again.'
              : 'The submission could not be started.',
          ),
        );
      } finally {
        setSubmitting(false);
      }
    },
    [beginRun, challenge, files, stream],
  );

  // Latest `startRun` for the shortcut listeners, which are registered once.
  const startRunRef = useRef(startRun);
  startRunRef.current = startRun;

  useEffect(() => {
    sessionStart.current = Date.now();
    const timer = setInterval(() => setSessionMs(Date.now() - sessionStart.current), TIMER_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [challenge]);

  useEffect(() => {
    if (!draftRestored) return;
    const timer = setTimeout(() => setDraftRestored(false), 4000);
    return () => clearTimeout(timer);
  }, [draftRestored]);

  // Run / Submit from the editor, the palette, or a bare key combo. Monaco handles
  // the combo itself when focused (and marks the event handled), so the window
  // listener ignores `defaultPrevented` events to avoid firing twice.
  useEffect(() => {
    const run = (): void => void startRunRef.current('run');
    const submit = (): void => void startRunRef.current('submit');
    const onKey = (event: KeyboardEvent): void => {
      if (event.defaultPrevented || !(event.metaKey || event.ctrlKey) || event.key !== 'Enter') return;
      event.preventDefault();
      if (event.shiftKey) submit();
      else run();
    };
    window.addEventListener(RUN_EVENT, run);
    window.addEventListener(SUBMIT_EVENT, submit);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener(RUN_EVENT, run);
      window.removeEventListener(SUBMIT_EVENT, submit);
      window.removeEventListener('keydown', onKey);
    };
  }, []);

  // Autosave the candidate's edits so a refresh or an accidental navigation never
  // costs them their work. Debounced; only differences from the starter are kept.
  useEffect(() => {
    if (challenge === null) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const unsubscribe = useIdeStore.subscribe((state, previous) => {
      if (state.files === previous.files) return;
      clearTimeout(timer);
      timer = setTimeout(() => saveDraft(challenge.slug, state.files), 400);
    });
    return () => {
      clearTimeout(timer);
      unsubscribe();
    };
  }, [challenge]);

  if (state === 'loading') {
    return (
      <div className="flex flex-1 flex-col gap-3 p-6" role="status" aria-label="Loading challenge">
        <div className="skeleton h-10 w-full" />
        <div className="skeleton flex-1" />
      </div>
    );
  }

  if (state === 'error') {
    return <LoadFailure error={error} onRetry={reload} />;
  }

  if (challenge === null) {
    return <LoadFailure error={new Error('No challenge returned.')} onRetry={reload} />;
  }

  const lang = LANGUAGE_META[challenge.language];
  const diff = DIFFICULTY_META[challenge.difficulty];

  const toolbar = (
    <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-edge bg-surface-raised px-3 py-2">
      <Link
        href="/challenges"
        className="inline-flex items-center gap-0.5 rounded-md py-1 pl-1 pr-2 text-xs text-content-muted transition-colors hover:bg-surface-overlay hover:text-content"
      >
        <ChevronLeft aria-hidden className="size-4" />
        Challenges
      </Link>

      <span aria-hidden className="h-4 w-px bg-edge-strong" />

      <span
        aria-hidden
        className="inline-flex h-6 min-w-6 items-center justify-center rounded-md px-1.5 font-mono text-[10px] font-bold"
        style={{
          color: lang.color,
          backgroundColor: `color-mix(in srgb, ${lang.color} 15%, transparent)`,
        }}
      >
        {lang.glyph}
      </span>
      <h1 className="min-w-0 truncate text-sm font-semibold">{sanitizeSingleLine(challenge.title, 100)}</h1>
      <span className={cn('hidden text-xs font-medium capitalize sm:inline', diff.text)}>
        {challenge.difficulty}
      </span>

      {draftRestored ? (
        <span role="status" className="animate-fade-up rounded-full bg-accent/15 px-2.5 py-0.5 text-[11px] font-medium text-accent">
          Draft restored
        </span>
      ) : null}

      {submitError !== null ? (
        <p role="alert" className="text-xs text-verdict-fail">
          {sanitizeSingleLine(submitError, 200)}
        </p>
      ) : null}

      <div className="ml-auto flex items-center gap-2">
        {/* `aria-live="off"`: a per-second announcement would make a screen reader
            unusable. The value is readable on demand. */}
        <span
          aria-live="off"
          title="Time on this challenge"
          className="hidden items-center gap-1.5 rounded-lg border border-edge bg-surface px-2.5 py-1.5 font-mono text-xs tabular-nums text-content-muted sm:inline-flex"
        >
          <Timer aria-hidden className="size-3.5 text-content-subtle" />
          <span className="sr-only">Time on task </span>
          {formatDuration(sessionMs)}
        </span>

        <button
          type="button"
          onClick={() => window.dispatchEvent(new Event(TOGGLE_SIDEBAR_EVENT))}
          title="Toggle task panel"
          aria-label="Toggle task panel"
          className="hidden size-8 items-center justify-center rounded-lg text-content-muted transition-colors hover:bg-surface-overlay hover:text-content lg:inline-flex"
        >
          <PanelLeft aria-hidden className="size-4" />
        </button>

        <Button
          size="sm"
          disabled={busy}
          onClick={() => void startRun('run')}
          title="Execute your code and see its output. Not graded, not recorded"
        >
          {busy && mode === 'run' ? <Spinner /> : <CirclePlay aria-hidden className="size-3.5" />}
          Run
          <Kbd keys={['mod', 'Enter']} className="ml-1 hidden md:inline-flex" />
        </Button>

        <Button
          size="sm"
          variant="primary"
          disabled={busy}
          onClick={() => void startRun('submit')}
          title="Grade against the hidden tests and record the attempt"
        >
          {busy && mode === 'submit' ? <Spinner /> : <Send aria-hidden className="size-3.5" />}
          Submit
        </Button>
      </div>
    </div>
  );

  return <IdeLayout challenge={challenge} toolbar={toolbar} />;
}

function LoadFailure({ error, onRetry }: { readonly error: unknown; readonly onRetry: () => void }): JSX.Element {
  const notFound = isApiError(error) && error.status === 404;
  const offline = isApiError(error) && error.isOffline;

  return (
    <div className="mx-auto w-full max-w-md px-4 py-16">
      <Card>
        <CardContent className="flex flex-col items-start gap-3">
          <ServerCrash aria-hidden className="size-6 text-verdict-infra" />
          <div>
            <h2 className="text-sm font-semibold">
              {notFound
                ? 'That challenge does not exist'
                : offline
                  ? 'Cannot reach the challenge service'
                  : 'Could not load this challenge'}
            </h2>
            <p className="mt-1 text-sm text-content-muted">
              {errorMessage(error, 'The request failed unexpectedly.')}
            </p>
          </div>
          <div className="flex gap-2">
            {notFound ? (
              <Link
                href="/challenges"
                className="rounded-md border border-edge bg-surface-raised px-3 py-2 text-sm hover:bg-surface-sunken"
              >
                Back to challenges
              </Link>
            ) : (
              <Button size="sm" onClick={onRetry}>
                Try again
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Draft persistence                                                           */
/* -------------------------------------------------------------------------- */

const draftKey = (slug: string): string => `gauntlet:draft:${slug}`;

/** Re-apply saved edits on top of the freshly loaded starter files. */
function restoreDraft(slug: string): boolean {
  try {
    const raw = localStorage.getItem(draftKey(slug));
    if (raw === null) return false;
    const draft: unknown = JSON.parse(raw);
    if (typeof draft !== 'object' || draft === null) return false;
    const { files, updateFile } = useIdeStore.getState();
    let applied = false;
    for (const [path, content] of Object.entries(draft)) {
      // Only paths that still exist: a stale draft must not resurrect removed files.
      if (typeof content === 'string' && files.some((file) => file.path === path)) {
        updateFile(path, content);
        applied = true;
      }
    }
    return applied;
  } catch {
    return false;
  }
}

function saveDraft(
  slug: string,
  files: readonly { readonly path: string; readonly content: string; readonly pristine: string }[],
): void {
  try {
    const changed: Record<string, string> = {};
    for (const file of files) {
      if (file.content !== file.pristine) changed[file.path] = file.content;
    }
    if (Object.keys(changed).length === 0) localStorage.removeItem(draftKey(slug));
    else localStorage.setItem(draftKey(slug), JSON.stringify(changed));
  } catch {
    /* Blocked or full storage: autosave is a convenience, never a requirement. */
  }
}
