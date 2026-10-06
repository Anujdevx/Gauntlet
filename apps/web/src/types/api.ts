/**
 * Domain types for the REST surface.
 *
 * Kept separate from `execution.ts` because that file is hand-written against the
 * *grading* contract, which is deliberately frozen: a drift in how verdicts are
 * shaped would silently corrupt mastery scoring. Everything here describes
 * catalogue and session data, which is cheaper to revise. Splitting the two lets
 * `pnpm gen:api` regenerate this file without touching the graded types.
 *
 * Fields are `readonly` throughout. The API is the only writer; the store owns
 * mutations and rebuilds whole objects instead of patching them, which makes
 * accidental in-place mutation of a cached response impossible.
 */

import type { ExecutionResult, ExecutionStatus } from './execution';

/** The signed-in candidate. The session itself lives in an httpOnly cookie. */
export interface User {
  readonly id: string;
  readonly email: string;
  readonly display_name: string;
}

/** Languages the runner can execute. Determines the editor's language mode. */
export type ChallengeLanguage = 'python' | 'react' | 'sql' | 'java';

/** Editorial difficulty label. Display-only: real difficulty is inferred from mastery. */
export type ChallengeDifficulty = 'easy' | 'medium' | 'hard';

/** Catalogue entry. Deliberately excludes the prompt and starter files so the
 *  challenge list never pulls multi-kilobyte payloads it will not render. */
export interface Challenge {
  readonly id: string;
  readonly slug: string;
  readonly title: string;
  readonly summary: string;
  readonly language: ChallengeLanguage;
  readonly difficulty: ChallengeDifficulty;
  readonly concept_nodes: readonly string[];
  readonly estimated_minutes: number;
  readonly tags: readonly string[];
}

/** Full challenge, including the candidate-visible prompt and starting files. */
export interface ChallengeDetail extends Challenge {
  readonly prompt: string;
  readonly starter_files: Readonly<Record<string, string>>;
}

/** `run` is a scratch execution; `submit` is recorded as evidence. */
export type SubmissionMode = 'run' | 'submit';

/**
 * `GET /submissions/{id}` can report `queued` because the endpoint returns before
 * the sandbox starts. The UI must therefore treat "not yet started" as a
 * distinct state rather than folding it into an `ExecutionStatus`.
 */
export type SubmissionState = ExecutionStatus | 'queued';

/** Accepted-submission envelope. 202, never blocking: execution is asynchronous. */
export interface SubmissionAccepted {
  readonly submission_id: string;
  readonly stream_key: string;
  readonly status: 'queued';
}

/** One node of the skill graph. `mastery` of 0 means the node is unseen. */
export interface SkillNode {
  /** Dot-separated path, e.g. `sql.joins.inner`. */
  readonly id: string;
  readonly label: string;
  readonly domain: string;
  /** 0 = domain root. Used for indentation; the parent link is authoritative. */
  readonly depth: number;
  readonly parent_id: string | null;
  /** Posterior mastery in [0, 1]. */
  readonly mastery: number;
  readonly attempts: number;
  readonly correct_attempts: number;
  readonly last_seen_at: string | null;
}

/** Request bodies. Grouped so a malformed call site is a type error. */
export interface Credentials {
  readonly email: string;
  readonly password: string;
}

export interface SignupInput extends Credentials {
  readonly display_name: string;
}

export interface CreateSubmissionInput {
  readonly challenge_id: string;
  readonly files: Readonly<Record<string, string>>;
  readonly mode: SubmissionMode;
}

/**
 * Envelope types.
 *
 * These mirror the API's `{ key: value }` responses rather than returning bare
 * arrays. Wrapping in a named object is what lets the backend add fields later
 * without breaking existing clients, and it makes an empty response
 * distinguishable from a successful one.
 */
/**
 * Envelope types.
 *
 * These mirror the API's `{ key: value }` responses rather than returning bare
 * arrays. Wrapping in a named object is what lets the backend add fields later
 * without breaking existing clients, and it makes an empty response
 * distinguishable from a successful one.
 */
export interface UserResponse {
  readonly user: User;
}

export interface ChallengeListResponse {
  readonly challenges: readonly Challenge[];
}

export interface ChallengeDetailResponse {
  readonly challenge: ChallengeDetail;
}

export interface SubmissionStatusResponse {
  readonly result: ExecutionResult | null;
  readonly status: SubmissionState;
}

export interface SkillsResponse {
  readonly nodes: readonly SkillNode[];
}

/** FastAPI's error shape. `detail` is a string for raised errors and an array
 *  of loc/msg objects for request-validation failures (422). */
export interface ApiErrorBody {
  readonly detail: string | readonly ValidationIssue[];
}

export interface ValidationIssue {
  readonly loc: readonly (string | number)[];
  readonly msg: string;
  readonly type: string;
}
