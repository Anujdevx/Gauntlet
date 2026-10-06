/**
 * Typed access to the runtime environment.
 *
 * Read lazily through functions rather than at module scope, because Next.js
 * inlines `process.env.NEXT_PUBLIC_*` only for statically analysable property
 * access. Destructuring into a module-level object breaks that and yields
 * `undefined` at runtime with no build error.
 */

/** Base URL for REST calls. Empty string means same-origin, which is correct behind a proxy. */
export function apiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_API_BASE_URL ?? '';
}

/** Base URL for the WebSocket that streams execution output. */
export function wsBaseUrl(): string {
  const explicit = process.env.NEXT_PUBLIC_WS_BASE_URL;
  if (explicit) {
    return explicit;
  }
  // Derive from the API base so a single variable configures both transports.
  // Wrong scheme is a common footgun: an https page may not open a ws:// socket.
  const base = apiBaseUrl();
  return base ? base.replace(/^http/, 'ws') : '';
}

/** Milliseconds before a WebSocket with no frames is considered dead. */
export const STREAM_IDLE_TIMEOUT_MS = 45_000;

/** Interval at which the UI polls when the WebSocket is unavailable. */
export const POLL_FALLBACK_INTERVAL_MS = 2_000;
