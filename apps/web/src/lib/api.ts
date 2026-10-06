/**
 * The only place the frontend touches the network.
 *
 * Centralised for three reasons that each fail badly when spread out:
 *  1. The session is an httpOnly cookie, so every call must send
 *     `credentials: 'include'`. Omitting it on one call produces a 401 that
 *     looks exactly like a logged-out session.
 *  2. A 401 means "no session", which must produce the same redirect from every
 *     call site. Two implementations drift.
 *  3. Streamed execution output arrives as untrusted JSON frames. Parsing those
 *     defensively in the transport layer keeps a malformed frame from crashing
 *     a component deep in the tree, and keeps the shape checks in one auditable
 *     spot.
 */
import { apiBaseUrl, wsBaseUrl } from '@/lib/env';
import type {
  ApiErrorBody,
  ChallengeDetailResponse,
  ChallengeListResponse,
  CreateSubmissionInput,
  Credentials,
  SignupInput,
  SkillsResponse,
  SubmissionAccepted,
  SubmissionStatusResponse,
  UserResponse,
  ValidationIssue,
} from '@/types/api';
import type {
  ExecutionEvent,
  ExecutionResult,
  ExecutionStatus,
  ScoreBreakdown,
  TestCaseResult,
} from '@/types/execution';

/** Path of the login route, used to build the post-expiry redirect. */
const LOGIN_PATH = '/login';

/**
 * Requests that must never trigger a login redirect on their own.
 *
 * `/auth/login` returning 401 means "wrong password", which must surface to the
 * candidate in the form. Redirecting there would erase the reason they are
 * looking at and bounce them to a page they are already on.
 */
const AUTH_PATHS: ReadonlySet<string> = new Set([
  '/api/v1/auth/login',
  '/api/v1/auth/signup',
  '/api/v1/auth/logout',
]);

const DEFAULT_TIMEOUT_MS = 20_000;

/** Shown when the request never reached the server, as distinct from an error response. */
export const NETWORK_ERROR_MESSAGE =
  'Could not reach the assessment service. Check that the API is running and try again.';

/**
 * A non-2xx response, or a transport failure.
 *
 * `status` is 0 when the request never completed, which lets callers distinguish
 * "the platform rejected this" from "the platform is unreachable" without
 * string-matching the message. The two warrant very different UI: one is the
 * candidate's problem to fix, the other is ours.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(`API ${status}: ${detail}`);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }

  /** True when the request never reached the server. */
  get isOffline(): boolean {
    return this.status === 0;
  }

  /** True when the session is missing or expired and the user must sign in. */
  get isUnauthorized(): boolean {
    return this.status === 401;
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

/** Message to display for any thrown value, with a caller-supplied fallback. */
export function errorMessage(value: unknown, fallback: string): string {
  if (isApiError(value)) {
    return value.detail;
  }
  if (value instanceof Error && value.name === 'AbortError') {
    return 'The request was cancelled.';
  }
  return fallback;
}

/* -------------------------------------------------------------------------- */
/* Low-level request                                                           */
/* -------------------------------------------------------------------------- */

export interface RequestOptions {
  readonly method?: 'GET' | 'POST';
  readonly body?: unknown;
  readonly signal?: AbortSignal;
  readonly timeoutMs?: number;
  /** Set for calls whose 401 is an expected outcome rather than an expired session. */
  readonly skipAuthRedirect?: boolean;
}

/**
 * Perform the fetch, wiring a timeout and an optional caller signal onto one
 * `AbortController`.
 *
 * A timeout is not optional: a sandbox that accepts a submission and then wedges
 * would otherwise leave a button spinning indefinitely with no way back. The
 * caller's signal is linked rather than replaced so page-level cancellation
 * (navigating away) still works during the timeout window.
 */
async function send(path: string, options: RequestOptions): Promise<Response> {
  const { method = 'GET', body, signal, timeoutMs = DEFAULT_TIMEOUT_MS } = options;
  const controller = new AbortController();
  const forwardAbort = (): void => controller.abort();
  if (signal?.aborted === true) {
    controller.abort();
  } else {
    signal?.addEventListener('abort', forwardAbort);
  }
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  const headers: Record<string, string> = { Accept: 'application/json' };
  let serialised: string | undefined;
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    serialised = JSON.stringify(body);
  }

  try {
    return await fetch(`${apiBaseUrl()}${path}`, {
      method,
      headers,
      ...(serialised === undefined ? {} : { body: serialised }),
      credentials: 'include',
      signal: controller.signal,
      // Assessment data is per-candidate and per-attempt; a cached catalogue
      // would be actively wrong after any mastery update.
      cache: 'no-store',
    });
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', forwardAbort);
  }
}

/**
 * Best-effort read of FastAPI's error body.
 *
 * `detail` is a string for raised errors but an array of `loc`/`msg` objects for
 * request-validation failures, so both shapes are handled. The status text is the
 * last resort, because "Request failed" tells a candidate nothing.
 */
async function readErrorDetail(response: Response): Promise<string> {
  let parsed: unknown = null;
  try {
    parsed = await response.json();
  } catch {
    parsed = null;
  }

  if (isRecord(parsed)) {
    const body = parsed as Partial<ApiErrorBody>;
    const detail: string | readonly ValidationIssue[] | undefined = body.detail;
    if (typeof detail === 'string' && detail.trim() !== '') {
      return detail;
    }
    if (isValidationIssueArray(detail)) {
      return detail.map(describeValidationIssue).join('; ');
    }
  }
  return fallbackDetailFor(response.status);
}

function isValidationIssueArray(value: unknown): value is readonly ValidationIssue[] {
  return Array.isArray(value) && value.every((entry) => isRecord(entry));
}

function describeValidationIssue(issue: unknown): string {
  if (!isRecord(issue)) {
    return 'Invalid request.';
  }
  const candidate = issue as Partial<ValidationIssue>;
  const loc = candidate.loc;
  // Not `Array.isArray(loc)`: that guard narrows to a mutable array, which a
  // `readonly` field type never matches, so the branch would not narrow at all.
  const field = isPathSegments(loc) ? loc.join('.') : '';
  const message = typeof candidate.msg === 'string' ? candidate.msg : 'Invalid value.';
  return field === '' ? message : `${field}: ${message}`;
}

function isPathSegments(value: unknown): value is readonly (string | number)[] {
  return (
    Array.isArray(value) &&
    value.every((entry) => typeof entry === 'string' || typeof entry === 'number')
  );
}

function fallbackDetailFor(status: number): string {
  if (status === 401) {
    return 'Your session has expired. Sign in again to continue.';
  }
  if (status === 403) {
    return 'You do not have access to that.';
  }
  if (status === 404) {
    return 'That item could not be found.';
  }
  if (status === 409) {
    return 'That conflicts with the current state. Try again.';
  }
  if (status === 422) {
    return 'Some of the submitted values were not valid.';
  }
  if (status >= 500) {
    return 'The assessment service reported an internal error.';
  }
  return `The request failed (HTTP ${status}).`;
}

/**
 * Send the candidate to the login page, preserving where they were.
 *
 * Guarded on both server and client: during SSR `window` is undefined, and a
 * redirect attempted there would throw instead of rendering. Guarded on the
 * current path as well, so an expired session cannot produce a redirect loop.
 */
function redirectToLogin(path: string): void {
  if (typeof window === 'undefined' || AUTH_PATHS.has(path)) {
    return;
  }
  const { pathname, search } = window.location;
  if (pathname === LOGIN_PATH || pathname.startsWith(`${LOGIN_PATH}/`)) {
    return;
  }
  window.location.assign(`${LOGIN_PATH}?next=${encodeURIComponent(`${pathname}${search}`)}`);
}

/** Issue a request, tolerating 204 responses that carry no body. */
async function requestOptional(
  path: string,
  options: RequestOptions = {},
): Promise<unknown | undefined> {
  let response: Response;
  try {
    response = await send(path, options);
  } catch (cause) {
    // A caller-driven abort is a deliberate cancellation, not a failure. Surface
    // it unchanged so the caller can ignore it instead of showing an error.
    if (options.signal?.aborted === true) {
      throw cause;
    }
    throw new ApiError(0, NETWORK_ERROR_MESSAGE);
  }

  if (response.status === 401 && options.skipAuthRedirect !== true) {
    redirectToLogin(path);
  }
  if (!response.ok) {
    throw new ApiError(response.status, await readErrorDetail(response));
  }
  if (response.status === 204) {
    return undefined;
  }
  try {
    return await response.json();
  } catch {
    throw new ApiError(response.status, 'The service returned a response that could not be read.');
  }
}

/** Issue a request that must produce a body. */
async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const payload = await requestOptional(path, options);
  if (payload === undefined) {
    throw new ApiError(204, 'The service returned an empty response where data was expected.');
  }
  return payload as T;
}

/* -------------------------------------------------------------------------- */
/* Auth                                                                        */
/* -------------------------------------------------------------------------- */

export function signup(input: SignupInput): Promise<UserResponse> {
  return request<UserResponse>('/api/v1/auth/signup', { method: 'POST', body: input });
}

export function login(input: Credentials): Promise<UserResponse> {
  return request<UserResponse>('/api/v1/auth/login', { method: 'POST', body: input });
}

export function logout(): Promise<void> {
  return requestOptional('/api/v1/auth/logout', { method: 'POST' }).then(() => undefined);
}

/**
 * Read the current session.
 *
 * `skipAuthRedirect` is set because this is the probe, not a protected action: a
 * 401 here is the normal answer for a signed-out visitor and must leave the page
 * renderable as a public page rather than redirecting.
 */
export function fetchCurrentUser(signal?: AbortSignal): Promise<UserResponse> {
  return request<UserResponse>('/api/v1/auth/me', {
    ...(signal === undefined ? {} : { signal }),
    skipAuthRedirect: true,
  });
}

/* -------------------------------------------------------------------------- */
/* Challenges                                                                  */
/* -------------------------------------------------------------------------- */

export function fetchChallenges(signal?: AbortSignal): Promise<ChallengeListResponse> {
  return request<ChallengeListResponse>('/api/v1/challenges', {
    ...(signal === undefined ? {} : { signal }),
  });
}

/** `slug` is URL-encoded: slugs are API-supplied and must never be interpolated raw. */
export function fetchChallenge(slug: string, signal?: AbortSignal): Promise<ChallengeDetailResponse> {
  return request<ChallengeDetailResponse>(
    `/api/v1/challenges/${encodeURIComponent(slug)}`,
    signal === undefined ? {} : { signal },
  );
}

/* -------------------------------------------------------------------------- */
/* Submissions                                                                 */
/* -------------------------------------------------------------------------- */

/**
 * Queue an execution.
 *
 * Returns as soon as the submission is accepted. The `stream_key` is not used by
 * the client: it is the server's handle for its own stream, and the WebSocket is
 * addressed by submission id. Receiving it confirms the pairing worked.
 */
export function createSubmission(input: CreateSubmissionInput): Promise<SubmissionAccepted> {
  return request<SubmissionAccepted>('/api/v1/submissions', {
    method: 'POST',
    body: {
      challenge_id: input.challenge_id,
      files: input.files,
      mode: input.mode,
    },
    // Queuing can outlast the normal request budget under load, since it waits
    // for a container from the warm pool.
    timeoutMs: 30_000,
  });
}

/** Polling fallback, used whenever the WebSocket is unavailable or stalls. */
export function fetchSubmissionStatus(
  submissionId: string,
  signal?: AbortSignal,
): Promise<SubmissionStatusResponse> {
  return request<SubmissionStatusResponse>(
    `/api/v1/submissions/${encodeURIComponent(submissionId)}`,
    signal === undefined ? {} : { signal },
  );
}

/** Absolute WebSocket URL for a submission's output stream. */
export function submissionStreamUrl(submissionId: string): string {
  const path = `/api/v1/submissions/${encodeURIComponent(submissionId)}/stream`;
  const base = wsBaseUrl();
  if (base === '') {
    // Same-origin: the browser resolves a relative ws: URL against the page's own
    // scheme and host, which is what the CSP `connect-src ws: wss:` allows.
    return path;
  }
  return `${base}${path}`;
}

/* -------------------------------------------------------------------------- */
/* Skills                                                                      */
/* -------------------------------------------------------------------------- */

export function fetchSkills(signal?: AbortSignal): Promise<SkillsResponse> {
  return request<SkillsResponse>('/api/v1/skills', {
    ...(signal === undefined ? {} : { signal }),
  });
}

/* -------------------------------------------------------------------------- */
/* Runtime narrowing of untrusted frames                                       */
/* -------------------------------------------------------------------------- */

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isStringOrNull(value: unknown): value is string | null {
  return value === null || typeof value === 'string';
}

function isNumberOrNull(value: unknown): value is number | null {
  return value === null || typeof value === 'number';
}

const EXECUTION_STATUSES: ReadonlySet<string> = new Set<string>([
  'passed',
  'failed',
  'error',
  'timeout',
  'resource_exceeded',
  'infra_error',
]);

function isExecutionStatus(value: unknown): value is ExecutionStatus {
  return typeof value === 'string' && EXECUTION_STATUSES.has(value);
}

function isStringArray(value: unknown): value is readonly string[] {
  return Array.isArray(value) && value.every((entry) => typeof entry === 'string');
}

function isTestCaseResult(value: unknown): value is TestCaseResult {
  if (!isRecord(value)) {
    return false;
  }
  return (
    typeof value['id'] === 'string' &&
    typeof value['name'] === 'string' &&
    typeof value['passed'] === 'boolean' &&
    typeof value['duration_ms'] === 'number' &&
    isStringOrNull(value['message']) &&
    isStringOrNull(value['stdout']) &&
    isStringOrNull(value['stderr']) &&
    isStringOrNull(value['stack_trace']) &&
    typeof value['skipped'] === 'boolean' &&
    isStringArray(value['param_ids'])
  );
}

function isScoreBreakdown(value: unknown): value is ScoreBreakdown {
  if (!isRecord(value)) {
    return false;
  }
  return (
    typeof value['passed'] === 'number' &&
    typeof value['failed'] === 'number' &&
    typeof value['skipped'] === 'number' &&
    typeof value['total'] === 'number' &&
    typeof value['ratio'] === 'number' &&
    typeof value['percentage'] === 'number'
  );
}

function isExecutionResult(value: unknown): value is ExecutionResult {
  if (!isRecord(value)) {
    return false;
  }
  return (
    typeof value['submission_id'] === 'string' &&
    isStringOrNull(value['question_id']) &&
    isExecutionStatus(value['status']) &&
    Array.isArray(value['tests']) &&
    value['tests'].every(isTestCaseResult) &&
    typeof value['stdout'] === 'string' &&
    typeof value['stderr'] === 'string' &&
    typeof value['runtime_ms'] === 'number' &&
    typeof value['stdout_truncated'] === 'boolean' &&
    typeof value['stderr_truncated'] === 'boolean' &&
    isScoreBreakdown(value['score']) &&
    isNumberOrNull(value['exit_code']) &&
    isStringOrNull(value['language']) &&
    isStringOrNull(value['image_digest']) &&
    typeof value['created_at'] === 'string' &&
    isNumberOrNull(value['duration_ms_total']) &&
    typeof value['counts_against_candidate'] === 'boolean'
  );
}

/**
 * Parse one text frame from the execution socket.
 *
 * Returns `null` for anything unrecognised instead of throwing. A stream that
 * carries one corrupt frame should degrade, not lose the run: the terminal
 * content and the verdict are both still worth having. `result` gets the
 * strictest treatment because it is the only frame that updates mastery.
 */
export function parseExecutionFrame(raw: string): ExecutionEvent | null {
  let decoded: unknown;
  try {
    decoded = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(decoded)) {
    return null;
  }
  const { event, data, seq } = decoded;
  if (typeof event !== 'string' || typeof seq !== 'number') {
    return null;
  }
  switch (event) {
    case 'stdout':
      // Each case constructs its own literal discriminant. Returning `{ event, … }`
      // from a shared `stdout | stderr` branch would widen `event` to a union and
      // TypeScript would reject it against the discriminated `ExecutionEvent`.
      return typeof data === 'string' ? { event: 'stdout', data, seq } : null;
    case 'stderr':
      return typeof data === 'string' ? { event: 'stderr', data, seq } : null;
    case 'status':
      return isExecutionStatus(data) ? { event: 'status', data, seq } : null;
    case 'result':
      return isExecutionResult(data) ? { event: 'result', data, seq } : null;
    case 'error':
      return typeof data === 'string' ? { event: 'error', data, seq } : null;
    default:
      return null;
  }
}
