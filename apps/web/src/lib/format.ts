/**
 * Presentation formatting and, more importantly, output sanitisation.
 *
 * Every string that reaches the terminal originates in a sandbox the candidate
 * controls. That makes it untrusted input that happens to be displayed rather
 * than parsed: ANSI escapes can repaint the page, rewrite earlier lines with
 * cursor movement, or hide text that a grader would have read. React escapes
 * markup but knows nothing about terminal control codes, so that responsibility
 * lands here rather than in each component.
 */

import type { ExecutionStatus } from '@/types/execution';

/* -------------------------------------------------------------------------- */
/* ANSI and control-character stripping                                         */
/* -------------------------------------------------------------------------- */

/**
 * Escape sequences a runner can emit.
 *
 * Four families, because `stripAnsi` runs on arbitrary runner output:
 *  - OSC (`ESC ] ... BEL|ST`) sets the window title and clipboard on some
 *    terminals. Long or unterminated, so it must be matched lazily.
 *  - DCS / SOS / PM / APC (`ESC P|X|^|_ ... ST`) carry arbitrary payloads and
 *    are the historical vector for clipboard and title spoofing.
 *  - CSI (`ESC [ ... final`) covers colours, cursor moves, and erase-line, which
 *    is the one that can visually delete a verdict.
 *  - Two-character escapes (`ESC` plus one byte) cover save/restore cursor.
 */
const ANSI_PATTERN = new RegExp(
  [
    '\\u001B\\][\\s\\S]*?(?:\\u0007|\\u001B\\\\)',
    '\\u001B[PXX^_][\\s\\S]*?(?:\\u001B\\\\|\\u0007)',
    '\\u001B\\[[0-?]*[ -/]*[@-~]',
    '\\u001B[@-Z\\\\-_]',
  ].join('|'),
  'g',
);

/**
 * C0 controls and DEL, excluding the two whitespace controls a text pane needs.
 * Tab is kept because it indents program output meaningfully. CR is deliberately
 * kept here so `foldCarriageReturns` can handle it first.
 */
const CONTROL_PATTERN = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g;

/**
 * Collapse `\r` progress redraws to the final state of each line.
 *
 * Test runners paint a progress line like `3/12 ..... 40%` and rewrite it with
 * `\r`. Rendered into a `<pre>` every intermediate frame would be concatenated
 * into one unreadable line, so keeping only the text after the last `\r` on each
 * line reproduces what the candidate would have seen in a real terminal.
 */
export function foldCarriageReturns(text: string): string {
  if (!text.includes('\r')) {
    return text;
  }
  return text
    .split('\n')
    .map((line) => {
      const lastReturn = line.lastIndexOf('\r');
      return lastReturn === -1 ? line : line.slice(lastReturn + 1);
    })
    .join('\n');
}

/**
 * Reduce untrusted runner output to inert plain text.
 *
 * Strips ANSI escapes, folds carriage returns, then removes leftover control
 * characters. The order matters: escape sequences must be removed before the
 * control-character pass, or the `ESC` byte of a sequence could be deleted and
 * leave its parameter bytes behind as visible garbage.
 */
export function stripAnsi(text: string): string {
  return foldCarriageReturns(text.replace(ANSI_PATTERN, '').replace(CONTROL_PATTERN, ''));
}

/**
 * Sanitise a value destined for a single-line label such as a test name.
 *
 * In addition to `stripAnsi` this collapses every run of whitespace, so a test
 * named `"a\n\n  FAIL"` cannot break out of its row and forge an adjacent
 * verdict, and caps the length so one pathological name cannot dominate the
 * panel.
 */
export function sanitizeSingleLine(text: string, maxLength = 200): string {
  const collapsed = stripAnsi(text).replace(/\s+/gu, ' ').trim();
  if (collapsed.length <= maxLength) {
    return collapsed;
  }
  return `${collapsed.slice(0, Math.max(0, maxLength - 1))}\u2026`;
}

/* -------------------------------------------------------------------------- */
/* Numbers, durations, percentages                                              */
/* -------------------------------------------------------------------------- */

const MS_PER_SECOND = 1_000;

/**
 * Human duration from milliseconds.
 *
 * Precision narrows as magnitude grows: two decimals below ten seconds, one
 * below a minute, whole seconds beyond. A grader reads hundreds of these per
 * session, and sub-millisecond noise in the small range is noise, not signal.
 */
export function formatDuration(milliseconds: number): string {
  if (!Number.isFinite(milliseconds) || milliseconds < 0) {
    return '\u2014';
  }
  // Exact zero is a real, reportable value (a timer that has not started, a test
  // that did no work). It must not be folded into the "<1ms" bucket, which reads
  // as "some non-zero amount too small to show".
  if (milliseconds === 0) {
    return '0s';
  }
  if (milliseconds < 1) {
    return '<1ms';
  }
  if (milliseconds < MS_PER_SECOND) {
    return `${Math.round(milliseconds)}ms`;
  }
  const seconds = milliseconds / MS_PER_SECOND;
  if (seconds < 60) {
    return `${seconds.toFixed(seconds < 10 ? 2 : 1)}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return `${minutes}m ${String(remainder).padStart(2, '0')}s`;
}

/** Format a ratio in [0, 1] as a whole-number percentage. */
export function formatPercentage(ratio: number): string {
  return `${formatPercentPoints(clampUnit(ratio) * 100)}%`;
}

/**
 * Format a value already expressed in percentage points.
 *
 * `ScoreBreakdown` carries both `ratio` and `percentage`, and the two can
 * disagree by rounding. Reading `percentage` where it exists avoids showing
 * `66%` for a score the backend computed as `0.666`.
 */
export function formatPercentPoints(points: number): string {
  if (!Number.isFinite(points)) {
    return '\u2014';
  }
  return String(Math.round(Math.min(100, Math.max(0, points))));
}

/** Format mastery for the numeric read-out beside its bar. Always two decimals. */
export function formatMastery(mastery: number): string {
  if (!Number.isFinite(mastery)) {
    return '\u2014';
  }
  return clampUnit(mastery).toFixed(2);
}

function clampUnit(value: number): number {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.min(1, Math.max(0, value));
}

/* -------------------------------------------------------------------------- */
/* Relative time                                                               */
/* -------------------------------------------------------------------------- */

const RELATIVE_UNITS: ReadonlyArray<readonly [Intl.RelativeTimeFormatUnit, number]> = [
  ['year', 31_536_000_000],
  ['month', 2_592_000_000],
  ['week', 604_800_000],
  ['day', 86_400_000],
  ['hour', 3_600_000],
  ['minute', 60_000],
  ['second', MS_PER_SECOND],
];

const RELATIVE_FORMATTER = new Intl.RelativeTimeFormat('en', { numeric: 'auto' });

/**
 * "3 days ago", or "never" for a node the candidate has not attempted.
 *
 * Returns a sentinel rather than blank space for unparseable input: an
 * indeterminate timestamp that renders as empty reads as "never seen", which is
 * a materially different claim about the candidate's history.
 */
export function formatRelativeTime(iso: string | null, now: number = Date.now()): string {
  if (iso === null || iso === '') {
    return 'never';
  }
  const then = Date.parse(iso);
  if (Number.isNaN(then)) {
    return 'unknown';
  }
  const delta = then - now;
  const magnitude = Math.abs(delta);
  for (const [unit, unitMs] of RELATIVE_UNITS) {
    if (magnitude >= unitMs || unit === 'second') {
      return RELATIVE_FORMATTER.format(Math.round(delta / unitMs), unit);
    }
  }
  return 'just now';
}

/* -------------------------------------------------------------------------- */
/* Status -> colour                                                            */
/* -------------------------------------------------------------------------- */

/**
 * Verdict tone. Fixed set rather than a colour string so components stay
 * theme-agnostic and the mapping from status to colour has exactly one home.
 *
 * Note which statuses map to `infra`: `error` and `infra_error` are both cases
 * where the harness or the platform failed. Neither is evidence about the
 * candidate, and colouring either red would tell them their code was wrong when
 * it may never have executed.
 */
export type StatusTone = 'pass' | 'fail' | 'skip' | 'infra';

const STATUS_TONES: Readonly<Record<ExecutionStatus, StatusTone>> = {
  passed: 'pass',
  failed: 'fail',
  error: 'infra',
  timeout: 'fail',
  resource_exceeded: 'fail',
  infra_error: 'infra',
};

const TONE_TEXT_CLASSES: Readonly<Record<StatusTone, string>> = {
  pass: 'text-verdict-pass',
  fail: 'text-verdict-fail',
  skip: 'text-verdict-skip',
  infra: 'text-verdict-infra',
};

const TONE_CHIP_CLASSES: Readonly<Record<StatusTone, string>> = {
  pass: 'border-verdict-pass/40 bg-verdict-pass/10 text-verdict-pass',
  fail: 'border-verdict-fail/40 bg-verdict-fail/10 text-verdict-fail',
  skip: 'border-verdict-skip/40 bg-verdict-skip/10 text-verdict-skip',
  infra: 'border-verdict-infra/40 bg-verdict-infra/10 text-verdict-infra',
};

const TONE_BAR_CLASSES: Readonly<Record<StatusTone, string>> = {
  pass: 'bg-verdict-pass',
  fail: 'bg-verdict-fail',
  skip: 'bg-verdict-skip',
  infra: 'bg-verdict-infra',
};

/** Tone for an execution status. Exhaustiveness is enforced by `Record`. */
export function statusTone(status: ExecutionStatus): StatusTone {
  return STATUS_TONES[status];
}

/** Foreground text class matching a status, for inline verdict text. */
export function statusTextClass(status: ExecutionStatus): string {
  return TONE_TEXT_CLASSES[STATUS_TONES[status]];
}

/** Border, background, and text together, for verdict chips and panels. */
export function statusChipClass(status: ExecutionStatus): string {
  return TONE_CHIP_CLASSES[STATUS_TONES[status]];
}

/** Background class for the filled portion of a meter or progress bar. */
export function toneBarClass(tone: StatusTone): string {
  return TONE_BAR_CLASSES[tone];
}

/** Text class matching a tone, when only a tone is available. */
export function toneTextClass(tone: StatusTone): string {
  return TONE_TEXT_CLASSES[tone];
}

/**
 * Band a mastery score for display.
 *
 * `unseen` is separate from a zero-valued score so the dashboard can treat a
 * node the candidate has never attempted differently from one they failed
 * repeatedly. Conflating the two is what makes weak skill models lie.
 */
export type MasteryBand = 'unseen' | 'weak' | 'developing' | 'strong';

export function masteryBand(mastery: number, attempts: number): MasteryBand {
  if (attempts <= 0 || mastery <= 0) {
    return 'unseen';
  }
  if (mastery < 0.4) {
    return 'weak';
  }
  if (mastery < 0.7) {
    return 'developing';
  }
  return 'strong';
}

const MASTERY_TONES: Readonly<Record<MasteryBand, StatusTone>> = {
  unseen: 'infra',
  weak: 'fail',
  developing: 'skip',
  strong: 'pass',
};

export function masteryToneClass(mastery: number, attempts: number): string {
  return toneTextClass(MASTERY_TONES[masteryBand(mastery, attempts)]);
}

export function masteryBarClass(mastery: number, attempts: number): string {
  return toneBarClass(MASTERY_TONES[masteryBand(mastery, attempts)]);
}
