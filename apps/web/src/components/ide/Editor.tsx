'use client';

/**
 * Monaco editor for the active challenge file.
 *
 * Three constraints shape this wrapper:
 *
 *  1. It is loaded through `dynamic(..., { ssr: false })`. Monaco touches
 *     `window` and `document` at module scope and resolves its workers
 *     asynchronously, so a server render either throws or ships a permanently
 *     blank editor. Client-only is not an optimisation here, it is a
 *     requirement.
 *  2. The theme follows the `.dark` class rather than a prop. The root layout
 *     toggles that class in a pre-paint script, and syncing to it means the
 *     editor matches the rest of the UI whether the candidate toggles the theme
 *     or has never touched it.
 *  3. `path` is passed so each file gets its own Monaco model. Without it,
 *     switching files reuses one model and the candidate loses the undo history
 *     of the file they just left.
 */
import { useEffect, useState } from 'react';
import dynamic from 'next/dynamic';

import { useIdeStore } from '@/store/ide';
import { Spinner } from '@/components/ui/Spinner';

/**
 * Monaco must not be bundled into the server or the initial route chunk. The
 * `.then(m => m.default)` wrapper is explicit because the package has both a
 * default export and named ones, and relying on `dynamic`'s implicit
 * default-export detection is a silent-failure risk.
 */
const MonacoEditor = dynamic(() => import('@monaco-editor/react').then((mod) => mod.default), {
  ssr: false,
  loading: () => (
    <div className="flex size-full items-center justify-center bg-surface-sunken">
      <Spinner label="Loading editor" />
    </div>
  ),
});

/** Maps a file extension to a Monaco language id. */
export function languageForPath(path: string): string {
  const dot = path.lastIndexOf('.');
  const extension = dot === -1 ? '' : path.slice(dot + 1).toLowerCase();
  switch (extension) {
    case 'py':
    case 'pyi':
      return 'python';
    case 'sql':
      return 'sql';
    case 'ts':
    case 'tsx':
    case 'js':
    case 'jsx':
    case 'mjs':
    case 'cjs':
      return 'typescript';
    case 'json':
      return 'json';
    case 'css':
      return 'css';
    case 'html':
      return 'html';
    case 'md':
    case 'mdx':
      return 'markdown';
    case 'yml':
    case 'yaml':
      return 'yaml';
    case 'sh':
      return 'shell';
    default:
      return 'plaintext';
  }
}

/*
 * Theme data is declared untyped and registered inside `beforeMount`, so
 * TypeScript contextually types `monaco` from the prop signature. That avoids
 * depending on which type exports the wrapper package happens to surface, which
 * have changed between its major versions.
 */
const DARK_THEME = {
  base: 'vs-dark',
  inherit: true,
  rules: [
    { token: 'comment', foreground: '6b7280', fontStyle: 'italic' },
    { token: 'keyword', foreground: 'c792ea' },
    { token: 'string', foreground: '8ce99a' },
    { token: 'number', foreground: 'f78c6c' },
  ],
  colors: {
    'editor.background': '0a0b0d',
    'editor.foreground': 'edeff1',
    'editorLineNumber.foreground': '3f4247',
    'editorLineNumber.activeForeground': '9ca3af',
    'editor.selectionBackground': '2a2e35',
    'editor.lineHighlightBackground': '16181c',
    'editorIndentGuide.background1': '272a2f',
  },
};

const LIGHT_THEME = {
  base: 'vs',
  inherit: true,
  rules: [{ token: 'comment', foreground: '6b7280', fontStyle: 'italic' }],
  colors: {
    'editor.background': 'ffffff',
    'editor.lineHighlightBackground': 'f9fafb',
    'editorIndentGuide.background1': 'e5e7eb',
  },
};

const DARK_THEME_ID = 'oa-dark';
const LIGHT_THEME_ID = 'oa-light';

/**
 * Track the `dark` class on `<html>`.
 *
 * A `MutationObserver` rather than a `matchMedia` listener, because the class is
 * the single source of truth once the pre-paint bootstrap has run. Observation is
 * also cheaper than a state subscription across the whole tree for something
 * that changes once per session.
 */
function useIsDark(): boolean {
  const [isDark, setIsDark] = useState(false);

  useEffect(() => {
    const root = document.documentElement;
    const sync = (): void => setIsDark(root.classList.contains('dark'));
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(root, { attributes: true, attributeFilter: ['class'] });
    return () => observer.disconnect();
  }, []);

  return isDark;
}

export interface EditorProps {
  /**
   * Namespaces Monaco's model registry per challenge. See the comment on
   * `modelUri` for why this cannot simply be the file path.
   */
  readonly scope: string;
}

export function Editor({ scope }: EditorProps): JSX.Element {
  const isDark = useIsDark();
  const activePath = useIdeStore((state) => state.activePath);
  const files = useIdeStore((state) => state.files);
  const updateFile = useIdeStore((state) => state.updateFile);

  const active = activePath === null ? undefined : files.find((file) => file.path === activePath);
  const path = active?.path ?? null;
  const value = active?.content ?? '';

  if (path === null) {
    return (
      <div className="flex size-full items-center justify-center bg-surface-sunken text-sm text-content-muted">
        This challenge has no starter files.
      </div>
    );
  }

  /*
   * Monaco keys its models by URI, and `@monaco-editor/react` uses the `path`
   * prop verbatim as that URI. Two challenges both containing `solution.py`
   * would therefore share one model, and the candidate would navigate into the
   * next challenge with the previous one's undo history attached. Namespacing
   * the URI by challenge keeps models isolated without remounting the editor.
   */
  const modelUri = `${scope}::${path}`;

  return (
    <MonacoEditor
      path={modelUri}
      language={languageForPath(path)}
      value={value}
      theme={isDark ? DARK_THEME_ID : LIGHT_THEME_ID}
      beforeMount={(monaco) => {
        monaco.editor.defineTheme(DARK_THEME_ID, DARK_THEME);
        monaco.editor.defineTheme(LIGHT_THEME_ID, LIGHT_THEME);
      }}
      onChange={(next) => updateFile(path, next ?? '')}
      options={{
        fontSize: 13,
        fontFamily: 'var(--font-mono)',
        fontLigatures: true,
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        // Smooth scrolling is a continuous animation during long reads; the
        // reduced-motion preference should not be the only thing standing
        // between a candidate and a vestibular trigger.
        smoothScrolling: false,
        renderLineHighlight: 'line',
        lineNumbersMinChars: 3,
        tabSize: 2,
        wordWrap: 'off',
        // Required because the editor's container is sized by the CSS grid
        // around it rather than by its own content.
        automaticLayout: true,
        padding: { top: 12, bottom: 12 },
        bracketPairColorization: { enabled: true },
        scrollbar: {
          verticalScrollbarSize: 10,
          horizontalScrollbarSize: 10,
          alwaysConsumeMouseWheel: false,
        },
      }}
    />
  );
}
