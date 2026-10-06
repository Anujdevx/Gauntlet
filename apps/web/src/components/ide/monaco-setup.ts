/**
 * Bundle Monaco with the app instead of letting `@monaco-editor/react` fetch it
 * from a CDN at runtime.
 *
 * The CDN default fails offline, on locked-down networks, and under this app's own
 * CSP, and it means a third party serves code that runs next to candidate input.
 * Loading it from the build keeps the editor working with no network at all and
 * lets the CSP stay `script-src 'self'`.
 *
 * Workers are created from the bundled ESM entrypoints; webpack turns each
 * `new Worker(new URL(...))` into its own chunk.
 */
import { loader } from '@monaco-editor/react';
import * as monaco from 'monaco-editor';

(globalThis as { MonacoEnvironment?: unknown }).MonacoEnvironment = {
  getWorker(_workerId: string, label: string): Worker {
    switch (label) {
      case 'json':
        return new Worker(new URL('monaco-editor/esm/vs/language/json/json.worker', import.meta.url));
      case 'css':
      case 'scss':
      case 'less':
        return new Worker(new URL('monaco-editor/esm/vs/language/css/css.worker', import.meta.url));
      case 'html':
      case 'handlebars':
      case 'razor':
        return new Worker(new URL('monaco-editor/esm/vs/language/html/html.worker', import.meta.url));
      case 'typescript':
      case 'javascript':
        return new Worker(new URL('monaco-editor/esm/vs/language/typescript/ts.worker', import.meta.url));
      default:
        return new Worker(new URL('monaco-editor/esm/vs/editor/editor.worker', import.meta.url));
    }
  },
};

loader.config({ monaco });
