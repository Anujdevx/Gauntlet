'use client';

/**
 * Minimal async-resource hook for page-level fetches.
 *
 * Exists so the four data pages share one cancellation story instead of each
 * reimplementing it. The details that matter:
 *
 *  - The request is aborted on unmount. Without it, navigating away mid-load
 *    resolves into a component that no longer exists and, worse, makes the user
 *    wait on the previous page's slowest request.
 *  - Errors are stored, not thrown. A failed fetch is an expected state here, and
 *    the API being unreachable is the normal condition during local development,
 *    so it must render as a panel rather than crash the route.
 *  - `reload` re-runs the loader without remounting, which is what a retry
 *    button needs.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

export type ResourceState = 'loading' | 'ready' | 'error';

export interface Resource<T> {
  readonly state: ResourceState;
  readonly data: T | null;
  readonly error: unknown;
  readonly reload: () => void;
}

export function useResource<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [state, setState] = useState<ResourceState>('loading');
  const [nonce, setNonce] = useState(0);

  /*
   * The loader is almost always a new closure each render. Holding it in a ref
   * and depending only on the caller's `deps` keeps the effect from refetching on
   * every render, which a naive `useEffect(..., [loader])` would do.
   */
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  useEffect(() => {
    const controller = new AbortController();
    setState('loading');
    setError(null);

    loaderRef
      .current(controller.signal)
      .then((value) => {
        if (controller.signal.aborted) {
          return;
        }
        setData(value);
        setState('ready');
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        // Keep whatever was previously loaded visible under the error so a failed
        // refresh does not blank the page.
        setError(cause);
        setState('error');
      });

    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((current) => current + 1), []);

  return { state, data, error, reload };
}
