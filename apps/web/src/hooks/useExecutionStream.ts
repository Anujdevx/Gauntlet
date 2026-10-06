'use client';

/**
 * Owns the transport for one execution: a WebSocket, a reconnect budget, and a
 * polling fallback.
 *
 * The design goal is that a candidate never sees a spinner with no explanation.
 * A stream can fail in three distinct ways and each needs a different response:
 * the socket never opens (proxy or CSP), it opens and drops (load balancer idle
 * timeout), or it stays open but stops sending frames (the execution wedged
 * before it produced a status). Only the last is detectable client-side, hence
 * the idle timer, and all three converge on the same place: polling, which the
 * REST endpoint already supports.
 *
 * The other hard requirement is that connections do not accumulate. `start`
 * cancels whatever came before it before opening anything, so holding down the
 * Run button leaves exactly one socket open rather than one per click.
 */
import { useCallback, useEffect, useRef } from 'react';

import {
  errorMessage,
  fetchSubmissionStatus,
  parseExecutionFrame,
  submissionStreamUrl,
} from '@/lib/api';
import { POLL_FALLBACK_INTERVAL_MS, STREAM_IDLE_TIMEOUT_MS } from '@/lib/env';
import { useIdeStore } from '@/store/ide';
import type { ConnectionState } from '@/store/ide';
import type { SubmissionState } from '@/types/api';

/** Reconnects attempted on an unexpected close before giving up on the socket. */
const MAX_RECONNECT_ATTEMPTS = 3;
const RECONNECT_BASE_MS = 400;
const RECONNECT_MAX_MS = 4_000;

/**
 * Consecutive failed polls tolerated before declaring the run unreachable.
 * Three at the poll interval is ~6s, long enough to ride out a pod restart and
 * short enough that a genuinely dead API is reported instead of spinning.
 */
const MAX_CONSECUTIVE_POLL_FAILURES = 3;

const POLL_FALLBACK_NOTICE = 'Live output is unavailable; waiting for the result instead.';

export interface ExecutionStream {
  /**
   * Begin streaming `submissionId`.
   *
   * The caller is responsible for `beginRun` in the store first: the run mode is
   * known only to the page that decided between Run and Submit, and duplicating
   * that decision here would let the store and the request disagree.
   */
  readonly start: (submissionId: string) => void;
  /** Tear down the current connection, if any. Safe to call when idle. */
  readonly cancel: () => void;
}

/** Exponential backoff with a ceiling, so a retry storm cannot outlive the page. */
function backoffMs(attempt: number): number {
  return Math.min(RECONNECT_BASE_MS * 2 ** Math.max(0, attempt - 1), RECONNECT_MAX_MS);
}

/** `queued` is the only non-terminal submission state. */
function isTerminalSubmissionStatus(status: SubmissionState): boolean {
  return status !== 'queued';
}

export function useExecutionStream(): ExecutionStream {
  /*
   * Every mutable piece of transport state lives in a ref rather than component
   * state on purpose. These are driven by socket and timer callbacks, which fire
   * outside React's render cycle; routing them through state would mean each
   * callback closes over a stale snapshot and a stale socket.
   */
  const socketRef = useRef<WebSocket | null>(null);
  const idleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const activeIdRef = useRef<string | null>(null);
  const attemptsRef = useRef(0);
  const pollFailuresRef = useRef(0);
  /** True once the run has produced a verdict or bailed out. Gates every callback. */
  const settledRef = useRef(false);
  /**
   * True once the run has been handed to the poller. Distinct from `settledRef`
   * because the run is still live and must keep being polled: this flag exists to
   * stop the abandoned socket's late `close` from starting a competing reconnect.
   */
  const pollingRef = useRef(false);
  const unmountedRef = useRef(false);

  const clearIdleTimer = useCallback((): void => {
    if (idleTimerRef.current !== null) {
      clearTimeout(idleTimerRef.current);
      idleTimerRef.current = null;
    }
  }, []);

  const clearReconnectTimer = useCallback((): void => {
    if (reconnectTimerRef.current !== null) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const clearPollTimer = useCallback((): void => {
    if (pollTimerRef.current !== null) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const closeSocket = useCallback((): void => {
    const socket = socketRef.current;
    socketRef.current = null;
    if (socket === null) {
      return;
    }
    // Detach before closing. `close` fires asynchronously, and a handler still
    // bound to the previous run would race the next run's store writes.
    socket.onopen = null;
    socket.onmessage = null;
    socket.onerror = null;
    socket.onclose = null;
    if (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN) {
      socket.close();
    }
  }, []);

  /** Stop all transport activity and freeze the run in a terminal state. */
  const settle = useCallback(
    (connection: Extract<ConnectionState, 'done' | 'error'>): void => {
      // Order matters: clearing the id first makes the in-flight `onclose` from
      // the socket we are about to close a no-op.
      activeIdRef.current = null;
      settledRef.current = true;
      pollingRef.current = true;
      clearIdleTimer();
      clearReconnectTimer();
      clearPollTimer();
      closeSocket();
      useIdeStore.getState().finishRun(connection);
    },
    [clearIdleTimer, clearReconnectTimer, clearPollTimer, closeSocket],
  );

  const pollOnce = useCallback(
    async (id: string): Promise<void> => {
      try {
        const response = await fetchSubmissionStatus(id);
        if (unmountedRef.current || settledRef.current || activeIdRef.current !== id) {
          return;
        }
        pollFailuresRef.current = 0;
        if (response.result !== null) {
          useIdeStore.getState().setResult(response.result);
          settle('done');
          return;
        }
        if (isTerminalSubmissionStatus(response.status)) {
          // A terminal status with no payload means the run ended but the result
          // could not be read. Settling as 'done' would render an empty verdict
          // panel and imply a pass, so this is reported as a failure to retrieve.
          useIdeStore
            .getState()
            .setStreamError('The run finished but its result could not be retrieved.');
          settle('error');
        }
      } catch (cause) {
        if (unmountedRef.current || settledRef.current || activeIdRef.current !== id) {
          return;
        }
        pollFailuresRef.current += 1;
        if (pollFailuresRef.current >= MAX_CONSECUTIVE_POLL_FAILURES) {
          useIdeStore.getState().setStreamError(errorMessage(cause, POLL_FALLBACK_NOTICE));
          settle('error');
        }
      }
    },
    [settle],
  );

  /**
   * Abandon the socket and poll instead.
   *
   * Idempotent: once polling is underway the run is flagged as no longer
   * socket-bound, so a late `close` from the abandoned socket cannot start a
   * second, competing recovery path.
   */
  const beginPolling = useCallback(
    (id: string, notice: string): void => {
      if (unmountedRef.current || settledRef.current || pollingRef.current || activeIdRef.current !== id) {
        return;
      }
      pollingRef.current = true;
      clearIdleTimer();
      clearReconnectTimer();
      closeSocket();
      pollTimerRef.current = setInterval(() => {
        void pollOnce(id);
      }, POLL_FALLBACK_INTERVAL_MS);
      const store = useIdeStore.getState();
      store.setConnection('polling');
      store.setStreamNotice(notice);
    },
    [clearIdleTimer, clearReconnectTimer, closeSocket, pollOnce],
  );

  /** Restart the liveness window. Called on every frame received. */
  const armIdleTimeout = useCallback(
    (id: string): void => {
      clearIdleTimer();
      idleTimerRef.current = setTimeout(() => {
        if (unmountedRef.current || settledRef.current || activeIdRef.current !== id) {
          return;
        }
        beginPolling(
          id,
          'The live stream stopped responding; switched to polling for the result.',
        );
      }, STREAM_IDLE_TIMEOUT_MS);
    },
    [beginPolling, clearIdleTimer],
  );

  /*
   * The explicit annotation is load-bearing. `openSocket` re-enters itself via
   * the reconnect timer, and without an annotation TypeScript would have to
   * infer a type for a variable referenced inside its own initialiser, which it
   * reports as an implicit `any`. Omitting it from the dependency array is
   * correct for the same reason: the function's identity is what the timer holds,
   * and it must not change.
   */
  const openSocket: (id: string) => void = useCallback(
    (id: string): void => {
      if (unmountedRef.current || settledRef.current || pollingRef.current || activeIdRef.current !== id) {
        return;
      }
      useIdeStore.getState().setConnection('connecting');

      let socket: WebSocket;
      try {
        socket = new WebSocket(submissionStreamUrl(id));
      } catch {
        // Thrown for a malformed URL or a browser that has no WebSocket at all.
        beginPolling(id, 'The live stream could not be opened; waiting for the result instead.');
        return;
      }
      socketRef.current = socket;
      armIdleTimeout(id);

      socket.onopen = () => {
        if (unmountedRef.current || settledRef.current || activeIdRef.current !== id) {
          return;
        }
        // Reset only on a successful open: a socket that opens then immediately
        // closes would otherwise consume the entire retry budget in one burst.
        attemptsRef.current = 0;
        useIdeStore.getState().setConnection('streaming');
      };

      socket.onmessage = (event: MessageEvent<unknown>) => {
        if (unmountedRef.current || settledRef.current || activeIdRef.current !== id) {
          return;
        }
        // Every frame counts as liveness, including one that fails to parse. A
        // socket emitting unexpected bytes is still a live connection, and
        // re-opening it would discard the good frames around it.
        armIdleTimeout(id);
        if (typeof event.data !== 'string') {
          return;
        }
        const frame = parseExecutionFrame(event.data);
        if (frame === null) {
          return;
        }
        const store = useIdeStore.getState();
        switch (frame.event) {
          case 'stdout':
            store.appendStdout(frame.data);
            break;
          case 'stderr':
            store.appendStderr(frame.data);
            break;
          case 'status':
            store.setLiveStatus(frame.data);
            break;
          case 'error':
            store.setStreamError(frame.data);
            settle('error');
            break;
          case 'result':
            store.setResult(frame.data);
            settle('done');
            break;
        }
      };

      socket.onerror = () => {
        // The DOM event carries no diagnostic detail and the browser has already
        // logged its own console error. `close` always follows and owns the
        // recovery decision, so handling it here too would mean two paths that
        // can disagree about whether to retry or to fall back.
      };

      socket.onclose = () => {
        if (unmountedRef.current || settledRef.current || activeIdRef.current !== id) {
          return;
        }
        clearIdleTimer();
        if (socketRef.current === socket) {
          socketRef.current = null;
        }
        // A result may already have arrived on an earlier socket in the retry
        // chain. Reconnecting then would open a stream for a finished run.
        if (useIdeStore.getState().result !== null) {
          settle('done');
          return;
        }
        if (attemptsRef.current < MAX_RECONNECT_ATTEMPTS) {
          attemptsRef.current += 1;
          reconnectTimerRef.current = setTimeout(() => {
            openSocket(id);
          }, backoffMs(attemptsRef.current));
          return;
        }
        beginPolling(id, 'The live stream closed; waiting for the result instead.');
      };
    },
    [armIdleTimeout, beginPolling, clearIdleTimer, settle],
  );

  const cancel = useCallback((): void => {
    activeIdRef.current = null;
    settledRef.current = true;
    pollingRef.current = true;
    clearIdleTimer();
    clearReconnectTimer();
    clearPollTimer();
    closeSocket();
    attemptsRef.current = 0;
    pollFailuresRef.current = 0;
    useIdeStore.getState().setConnection('idle');
  }, [clearIdleTimer, clearReconnectTimer, clearPollTimer, closeSocket]);

  const start = useCallback(
    (submissionId: string): void => {
      // Always cancel first. This is what makes a rapid second Run click safe:
      // the previous socket, idle timer, reconnect timer, and poll timer are all
      // released before the new connection is opened.
      cancel();
      if (unmountedRef.current) {
        return;
      }
      activeIdRef.current = submissionId;
      settledRef.current = false;
      pollingRef.current = false;
      attemptsRef.current = 0;
      pollFailuresRef.current = 0;
      openSocket(submissionId);
    },
    [cancel, openSocket],
  );

  useEffect(() => {
    unmountedRef.current = false;
    return () => {
      // Set before cancelling so any callback that fires during teardown bails
      // out rather than writing to a store nobody is rendering.
      unmountedRef.current = true;
      cancel();
    };
  }, [cancel]);

  return { start, cancel };
}
