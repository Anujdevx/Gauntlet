/**
 * Types mirroring the API's unified execution result.
 *
 * Hand-written rather than generated so the frontend compiles without a running
 * backend, and so a schema drift is a visible TypeScript error rather than a
 * silent `any`. `pnpm gen:api` (Phase 2) will emit these from the OpenAPI
 * document; until then this file is the contract and the CI diff job will
 * compare them.
 */

/** Terminal state of a sandboxed run. Mirrors `ExecutionStatus` in the backend. */
export type ExecutionStatus =
  | 'passed'
  | 'failed'
  | 'error'
  | 'timeout'
  | 'resource_exceeded'
  | 'infra_error';

/**
 * Whether a status may be treated as evidence about the candidate.
 *
 * `infra_error` is false, and the UI must reflect that: a platform fault is not
 * a skill gap and must never be shown as a failed question.
 */
export const COUNTS_AGAINST_CANDIDATE: ReadonlySet<ExecutionStatus> = new Set([
  'passed',
  'failed',
  'timeout',
  'resource_exceeded',
]);

/** Human-facing label for each status, used in the test panel header. */
export const STATUS_LABELS: Readonly<Record<ExecutionStatus, string>> = {
  passed: 'All tests passed',
  failed: 'Tests failed',
  error: 'Could not run tests',
  timeout: 'Timed out',
  resource_exceeded: 'Resource limit exceeded',
  infra_error: 'Platform error — not your code',
};

/** Outcome of one hidden test. */
export interface TestCaseResult {
  readonly id: string;
  readonly name: string;
  readonly passed: boolean;
  readonly duration_ms: number;
  readonly message: string | null;
  readonly stdout: string | null;
  readonly stderr: string | null;
  readonly stack_trace: string | null;
  readonly skipped: boolean;
  readonly param_ids: readonly string[];
}

/** Deterministic score derived only from per-test verdicts. */
export interface ScoreBreakdown {
  readonly passed: number;
  readonly failed: number;
  readonly skipped: number;
  readonly total: number;
  readonly ratio: number;
  readonly percentage: number;
}

/** The single result shape the API serves for every language. */
export interface ExecutionResult {
  readonly submission_id: string;
  readonly question_id: string | null;
  readonly status: ExecutionStatus;
  readonly tests: readonly TestCaseResult[];
  readonly stdout: string;
  readonly stderr: string;
  readonly runtime_ms: number;
  readonly stdout_truncated: boolean;
  readonly stderr_truncated: boolean;
  readonly score: ScoreBreakdown;
  readonly exit_code: number | null;
  readonly language: string | null;
  readonly image_digest: string | null;
  readonly created_at: string;
  readonly duration_ms_total: number | null;
  readonly counts_against_candidate: boolean;
}

/** A chunk of streamed output or a terminal result, over WebSocket. */
export type ExecutionEvent =
  | { readonly event: 'stdout'; readonly data: string; readonly seq: number }
  | { readonly event: 'stderr'; readonly data: string; readonly seq: number }
  | { readonly event: 'status'; readonly data: ExecutionStatus; readonly seq: number }
  | { readonly event: 'result'; readonly data: ExecutionResult; readonly seq: number }
  | { readonly event: 'error'; readonly data: string; readonly seq: number };

/** Whether a result counts as evidence for mastery updates. */
export function countsAgainstCandidate(status: ExecutionStatus): boolean {
  return COUNTS_AGAINST_CANDIDATE.has(status);
}
