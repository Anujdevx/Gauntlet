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
import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { ArrowLeft, CirclePlay, Send, ServerCrash } from 'lucide-react';

import { AppShell } from '@/components/layout/AppShell';
import { Button } from '@/components/ui/Button';
import { Card, CardContent } from '@/components/ui/Card';
import { Spinner } from '@/components/ui/Spinner';
import { IdeLayout } from '@/components/ide/IdeLayout';
import { errorMessage, createSubmission, fetchChallenge, isApiError } from '@/lib/api';
import { formatDuration, sanitizeSingleLine } from '@/lib/format';
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
  const elapsedMs = useIdeStore((store) => store.elapsedMs);
  const connection = useIdeStore((store) => store.connection);
  const files = useIdeStore((store) => store.files);
  const mode = useIdeStore((store) => store.mode);

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

  if (state === 'loading') {
    return (
      <div className="flex flex-1 items-center justify-center">
        <Spinner label="Loading challenge" />
      </div>
    );
  }

  if (state === 'error') {
    return <LoadFailure error={error} onRetry={reload} />;
  }

  if (challenge === null) {
    return <LoadFailure error={new Error('No challenge returned.')} onRetry={reload} />;
  }

  const toolbar = (
    <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-edge bg-surface-raised px-3 py-2">
      <Link
        href="/challenges"
        className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-xs text-content-muted hover:bg-surface-sunken hover:text-content"
      >
        <ArrowLeft aria-hidden className="size-3.5" />
        All challenges
      </Link>

      <h1 className="ml-1 min-w-0 truncate text-sm font-semibold">
        {sanitizeSingleLine(challenge.title, 100)}
      </h1>

      {submitError !== null ? (
        <p role="alert" className="text-xs text-verdict-fail">
          {sanitizeSingleLine(submitError, 200)}
        </p>
      ) : null}

      <div className="ml-auto flex items-center gap-2">
        {/*
          `aria-live="off"` is deliberate: a per-second timer announcement would
          make a screen reader unusable for the duration of a run. The value is
          readable on demand and announced only when it settles.
        */}
        <span
          aria-live="off"
          className="rounded border border-edge bg-surface px-2 py-1 font-mono text-xs tabular-nums text-content-muted"
        >
          <span className="sr-only">Elapsed time </span>
          {formatDuration(elapsedMs)}
        </span>

        <Button
          size="sm"
          disabled={busy}
          onClick={() => void startRun('run')}
          title="Run against the visible checks without recording the attempt"
        >
          {busy && mode === 'run' ? <Spinner /> : <CirclePlay aria-hidden className="size-3.5" />}
          Run
        </Button>

        <Button
          size="sm"
          variant="primary"
          disabled={busy}
          onClick={() => void startRun('submit')}
          title="Record this attempt and update your skill profile"
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
