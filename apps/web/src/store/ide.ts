/**
 * IDE state: open buffers, streamed output, and the in-flight execution.
 *
 * All of it lives in one store because a run, the buffers it writes to, and the
 * verdict it eventually produces are a single object with a lifecycle. Splitting
 * them across stores would mean a component could observe a result whose
 * matching stdout had already been cleared by a subsequent run.
 *
 * Two defensive choices are load-bearing here:
 *  - Output buffers are capped and the tail is kept, because the end of a log is
 *    where the failure is and the beginning is where a 200 KB stack dump lives.
 *  - Truncation is recorded as an explicit flag the UI must surface. Silently
 *    dropping candidate-visible output would let them conclude their code ran
 *    cleanly when the runner said otherwise.
 */
import { create } from 'zustand';

import type { ExecutionResult, ExecutionStatus } from '@/types/execution';
import type { SubmissionMode } from '@/types/api';

/**
 * Transport and lifecycle state of the current run.
 *
 * `polling` is a distinct state rather than a boolean because a candidate
 * watching a stalled spinner cannot tell a slow run from a broken stream, and
 * "result not yet available" needs different wording from "we lost the socket".
 */
export type ConnectionState = 'idle' | 'connecting' | 'streaming' | 'polling' | 'done' | 'error';

/** Per-buffer cap. Roughly 200 KB of UTF-16 text. */
export const OUTPUT_LIMIT_CHARS = 200_000;

export interface EditorFile {
  readonly path: string;
  readonly content: string;
  /** Content at load time, used to compute the modified indicator. */
  readonly pristine: string;
}

export interface IdeState {
  readonly files: readonly EditorFile[];
  /**
   * File paths only, in the same order as `files`.
   *
   * Kept separate so the explorer can memoise its tree on this. Selecting `files`
   * and memoising on it would rebuild the whole hierarchy on every keystroke,
   * because each edit produces a new array.
   */
  readonly paths: readonly string[];
  readonly activePath: string | null;
  readonly stdout: string;
  readonly stderr: string;
  readonly stdoutTruncated: boolean;
  readonly stderrTruncated: boolean;
  readonly result: ExecutionResult | null;
  /** Latest status frame seen on the stream, before a result arrives. */
  readonly liveStatus: ExecutionStatus | null;
  readonly connection: ConnectionState;
  readonly submissionId: string | null;
  readonly mode: SubmissionMode | null;
  readonly streamError: string | null;
  /**
   * Degraded-transport notice, kept separate from `streamError` on purpose.
   * "Live output is unavailable, polling instead" is not a failure and must not
   * be styled or worded like one, or a candidate reads a network hiccup as a
   * broken submission.
   */
  readonly streamNotice: string | null;
  readonly startedAt: number | null;
  readonly elapsedMs: number;
  /** Caret position in the active editor, shown in the status bar. 1-based. */
  readonly cursor: { readonly line: number; readonly column: number };

  readonly setCursor: (line: number, column: number) => void;
  /** Restore every file to its starter content. */
  readonly resetFiles: () => void;
  readonly loadChallengeFiles: (files: Readonly<Record<string, string>>) => void;
  readonly updateFile: (path: string, content: string) => void;
  readonly selectFile: (path: string) => void;
  readonly reset: () => void;
  readonly clearOutput: () => void;

  readonly beginRun: (submissionId: string, mode: SubmissionMode, now: number) => void;
  readonly appendStdout: (chunk: string) => void;
  readonly appendStderr: (chunk: string) => void;
  readonly setConnection: (connection: ConnectionState) => void;
  readonly setLiveStatus: (status: ExecutionStatus) => void;
  readonly setStreamError: (message: string) => void;
  readonly setStreamNotice: (message: string | null) => void;
  /** Install a terminal result, seeding buffers and truncation flags from it. */
  readonly setResult: (result: ExecutionResult) => void;
  readonly finishRun: (connection: Extract<ConnectionState, 'done' | 'error'>) => void;

  readonly startTimer: (now: number) => void;
  readonly tickTimer: (now: number) => void;
  readonly stopTimer: () => void;
}

/**
 * Append with a hard ceiling, keeping the most recent characters.
 *
 * Truncating from the front rather than the back is the deliberate choice: a
 * runner's final lines carry the exception and the exit, whereas its first lines
 * are usually a dependency banner.
 */
function appendCapped(current: string, chunk: string): { text: string; truncated: boolean } {
  if (chunk === '') {
    return { text: current, truncated: current.length > OUTPUT_LIMIT_CHARS };
  }
  const next = current + chunk;
  if (next.length <= OUTPUT_LIMIT_CHARS) {
    return { text: next, truncated: false };
  }
  return { text: next.slice(next.length - OUTPUT_LIMIT_CHARS), truncated: true };
}

const EMPTY_FILES: readonly EditorFile[] = [];
const EMPTY_PATHS: readonly string[] = [];

const INITIAL = {
  files: EMPTY_FILES,
  paths: EMPTY_PATHS,
  activePath: null,
  stdout: '',
  stderr: '',
  stdoutTruncated: false,
  stderrTruncated: false,
  result: null,
  liveStatus: null,
  connection: 'idle',
  submissionId: null,
  mode: null,
  streamError: null,
  streamNotice: null,
  startedAt: null,
  elapsedMs: 0,
  cursor: { line: 1, column: 1 },
} as const;

export const useIdeStore = create<IdeState>()((set, get) => ({
  ...INITIAL,

  loadChallengeFiles: (files) => {
    // Sorted so the explorer and the default selection are stable across reloads;
    // an unstable order makes the keyboard cursor jump between visits.
    const entries = Object.keys(files)
      .sort((left, right) => (left < right ? -1 : left > right ? 1 : 0))
      .map((path) => ({ path, content: files[path] ?? '', pristine: files[path] ?? '' }));
    const first = entries[0];
    set({
      files: entries,
      paths: entries.map((entry) => entry.path),
      activePath: first === undefined ? null : first.path,
      stdout: '',
      stderr: '',
      stdoutTruncated: false,
      stderrTruncated: false,
      result: null,
      liveStatus: null,
      connection: 'idle',
      submissionId: null,
      mode: null,
      streamError: null,
      streamNotice: null,
      startedAt: null,
      elapsedMs: 0,
    });
  },

  setCursor: (line, column) => set({ cursor: { line, column } }),

  resetFiles: () =>
    set((state) => ({
      files: state.files.map((file) => ({ ...file, content: file.pristine })),
    })),

  updateFile: (path, content) => {
    set((state) => ({
      files: state.files.map((file) => (file.path === path ? { ...file, content } : file)),
    }));
  },

  selectFile: (path) => set({ activePath: path }),

  reset: () => set({ ...INITIAL }),

  clearOutput: () =>
    set({ stdout: '', stderr: '', stdoutTruncated: false, stderrTruncated: false }),

  beginRun: (submissionId, mode, now) =>
    set({
      // Buffers are cleared per run, not per challenge: comparing output across
      // two attempts of the same code is rarely meaningful and mixing them is
      // actively misleading.
      stdout: '',
      stderr: '',
      stdoutTruncated: false,
      stderrTruncated: false,
      result: null,
      liveStatus: null,
      streamError: null,
      streamNotice: null,
      connection: 'connecting',
      submissionId,
      mode,
      startedAt: now,
      elapsedMs: 0,
    }),

  appendStdout: (chunk) =>
    set((state) => {
      const next = appendCapped(state.stdout, chunk);
      return { stdout: next.text, stdoutTruncated: state.stdoutTruncated || next.truncated };
    }),

  appendStderr: (chunk) =>
    set((state) => {
      const next = appendCapped(state.stderr, chunk);
      return { stderr: next.text, stderrTruncated: state.stderrTruncated || next.truncated };
    }),

  setConnection: (connection) => set({ connection }),

  setLiveStatus: (status) => set({ liveStatus: status }),

  setStreamError: (message) => set({ streamError: message }),

  setStreamNotice: (message) => set({ streamNotice: message }),

  setResult: (result) => {
    const state = get();
    // When the result arrived without a preceding stream (polling fallback, or a
    // run that produced no live frames), adopt its output so the terminal is not
    // empty. Guarded on emptiness: during live streaming the same bytes are
    // already buffered and re-adopting would duplicate them.
    const adoptStdout = state.stdout === '' ? result.stdout : state.stdout;
    const adoptStderr = state.stderr === '' ? result.stderr : state.stderr;
    set({
      result,
      liveStatus: result.status,
      stdout: adoptStdout,
      stderr: adoptStderr,
      stdoutTruncated: state.stdoutTruncated || result.stdout_truncated,
      stderrTruncated: state.stderrTruncated || result.stderr_truncated,
      connection: 'done',
    });
  },

  finishRun: (connection) =>
    set((state) => ({
      connection,
      elapsedMs:
        state.startedAt === null ? state.elapsedMs : Math.max(0, Date.now() - state.startedAt),
    })),

  startTimer: (now) => set({ startedAt: now, elapsedMs: 0 }),

  tickTimer: (now) => {
    const startedAt = get().startedAt;
    if (startedAt === null) {
      return;
    }
    set({ elapsedMs: Math.max(0, now - startedAt) });
  },

  stopTimer: () =>
    set((state) => ({
      startedAt: null,
      elapsedMs: state.startedAt === null ? state.elapsedMs : Math.max(0, Date.now() - state.startedAt),
    })),
}));
