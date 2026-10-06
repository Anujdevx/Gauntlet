'use client';

/**
 * Monaco editor for the active challenge file, with a file-tab strip.
 *
 * Three constraints shape this wrapper:
 *
 *  1. Monaco is loaded through `dynamic(..., { ssr: false })`: it touches `window`
 *     at module scope, so a server render either throws or ships a blank editor.
 *  2. Themes follow the `.dark` class on `<html>`, so the editor matches the rest
 *     of the UI however the candidate toggles it.
 *  3. `path` is namespaced per challenge so each file gets its own Monaco model
 *     (and its own undo history) without models leaking between challenges.
 *
 * Keyboard shortcuts are registered inside Monaco because the editor consumes
 * key events before the window sees them; they re-emit window events the
 * workspace listens for.
 */
import { useEffect, useState } from 'react';
import dynamic from 'next/dynamic';
import type { editor as MonacoNs } from 'monaco-editor';
import { FileCode2, RotateCcw } from 'lucide-react';

import { cn } from '@/lib/cn';
import { PALETTE_EVENT, RUN_EVENT, SUBMIT_EVENT, TOGGLE_SIDEBAR_EVENT } from '@/lib/shortcuts';
import { useIdeStore } from '@/store/ide';

const MonacoEditor = dynamic(
  async () => {
    // Order matters: the bundled Monaco must be registered with the loader before
    // the React wrapper first asks for it, or it falls back to the CDN.
    await import('@/components/ide/monaco-setup');
    return (await import('@monaco-editor/react')).default;
  },
  {
    ssr: false,
    loading: () => <EditorSkeleton />,
  },
);

function EditorSkeleton(): JSX.Element {
  return (
    <div className="flex size-full flex-col gap-3 bg-surface p-6" role="status" aria-label="Loading editor">
      {[60, 40, 75, 30, 55].map((width, index) => (
        <div key={index} className="skeleton h-3" style={{ width: `${width}%` }} />
      ))}
    </div>
  );
}

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
    case 'java':
      return 'java';
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

const DARK_THEME: MonacoNs.IStandaloneThemeData = {
  base: 'vs-dark',
  inherit: true,
  rules: [
    { token: 'comment', foreground: '5c6370', fontStyle: 'italic' },
    { token: 'keyword', foreground: 'ff9e64' },
    { token: 'keyword.sql', foreground: 'ff9e64' },
    { token: 'string', foreground: '9ece6a' },
    { token: 'number', foreground: 'ffc777' },
    { token: 'type', foreground: '2ac3de' },
    { token: 'type.identifier', foreground: '2ac3de' },
    { token: 'predefined', foreground: '7aa2f7' },
    { token: 'operator', foreground: '89ddff' },
    { token: 'delimiter', foreground: '9aa5b8' },
    { token: 'tag', foreground: 'f7768e' },
    { token: 'attribute.name', foreground: 'e0af68' },
  ],
  colors: {
    'editor.background': '#0b0b0e',
    'editor.foreground': '#e6e6ea',
    'editorLineNumber.foreground': '#3a3a46',
    'editorLineNumber.activeForeground': '#ff7a29',
    'editor.selectionBackground': '#ff7a2933',
    'editor.inactiveSelectionBackground': '#ff7a2318',
    'editor.lineHighlightBackground': '#14141a',
    'editor.lineHighlightBorder': '#00000000',
    'editorCursor.foreground': '#ff7a29',
    'editorIndentGuide.background1': '#1d1d25',
    'editorIndentGuide.activeBackground1': '#2f2f3b',
    'editorBracketMatch.background': '#ff7a2922',
    'editorBracketMatch.border': '#ff7a2966',
    'editorWidget.background': '#16161c',
    'editorWidget.border': '#26262f',
    'editorSuggestWidget.selectedBackground': '#ff7a2922',
    'scrollbarSlider.background': '#2a2a3466',
    'scrollbarSlider.hoverBackground': '#3a3a4688',
  },
};

const LIGHT_THEME: MonacoNs.IStandaloneThemeData = {
  base: 'vs',
  inherit: true,
  rules: [
    { token: 'comment', foreground: '8b8b94', fontStyle: 'italic' },
    { token: 'keyword', foreground: 'c2410c' },
    { token: 'string', foreground: '3f7d20' },
    { token: 'number', foreground: 'b45309' },
    { token: 'type', foreground: '0e7490' },
    { token: 'predefined', foreground: '2563eb' },
  ],
  colors: {
    'editor.background': '#fcfcfb',
    'editor.lineHighlightBackground': '#f4f4f1',
    'editor.lineHighlightBorder': '#00000000',
    'editorLineNumber.activeForeground': '#ea580c',
    'editorCursor.foreground': '#ea580c',
    'editor.selectionBackground': '#ea580c26',
    'editorIndentGuide.background1': '#e8e8e3',
  },
};

const DARK_THEME_ID = 'gauntlet-dark';
const LIGHT_THEME_ID = 'gauntlet-light';

/** Track the `dark` class on `<html>`. */
function useIsDark(): boolean {
  const [isDark, setIsDark] = useState(true);

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

/** Monaco measures glyphs from the family name, so it gets the literal stack, not a CSS var. */
const EDITOR_FONT = "'JetBrains Mono Variable', ui-monospace, 'SF Mono', Menlo, monospace";

export interface EditorProps {
  /** Namespaces Monaco's model registry per challenge. */
  readonly scope: string;
}

export function Editor({ scope }: EditorProps): JSX.Element {
  const isDark = useIsDark();
  const activePath = useIdeStore((state) => state.activePath);
  const files = useIdeStore((state) => state.files);
  const updateFile = useIdeStore((state) => state.updateFile);
  const selectFile = useIdeStore((state) => state.selectFile);
  const setCursor = useIdeStore((state) => state.setCursor);
  const resetFiles = useIdeStore((state) => state.resetFiles);
  const [confirmReset, setConfirmReset] = useState(false);

  const active = activePath === null ? undefined : files.find((file) => file.path === activePath);
  const path = active?.path ?? null;
  const value = active?.content ?? '';
  const anyModified = files.some((file) => file.content !== file.pristine);

  // A pending confirmation should not linger if the candidate wanders off.
  useEffect(() => {
    if (!confirmReset) return;
    const timer = setTimeout(() => setConfirmReset(false), 3000);
    return () => clearTimeout(timer);
  }, [confirmReset]);

  if (path === null) {
    return (
      <div className="flex size-full items-center justify-center bg-surface-sunken text-sm text-content-muted">
        This challenge has no starter files.
      </div>
    );
  }

  const modelUri = `${scope}::${path}`;

  return (
    <div className="flex size-full min-h-0 flex-col">
      <div
        role="tablist"
        aria-label="Open files"
        className="scrollbar-thin flex shrink-0 items-end gap-px overflow-x-auto border-b border-edge bg-surface-sunken px-2 pt-1.5"
      >
        {files.map((file) => {
          const isActive = file.path === path;
          const name = file.path.split('/').pop() ?? file.path;
          const modified = file.content !== file.pristine;
          return (
            <button
              key={file.path}
              type="button"
              role="tab"
              aria-selected={isActive}
              title={file.path}
              onClick={() => selectFile(file.path)}
              className={cn(
                'group relative inline-flex h-8 items-center gap-2 rounded-t-lg px-3.5 text-xs transition-colors',
                isActive
                  ? 'bg-surface text-content'
                  : 'text-content-subtle hover:bg-surface/50 hover:text-content-muted',
              )}
            >
              {isActive ? (
                <span aria-hidden className="absolute inset-x-2 top-0 h-0.5 rounded-b bg-accent" />
              ) : null}
              <FileCode2 aria-hidden className={cn('size-3.5', isActive && 'text-accent')} />
              <span className="font-mono">{name}</span>
              {modified ? (
                <span aria-label="modified" className="size-1.5 rounded-full bg-accent" />
              ) : null}
            </button>
          );
        })}

        <button
          type="button"
          disabled={!anyModified}
          onClick={() => {
            if (confirmReset) {
              resetFiles();
              setConfirmReset(false);
            } else {
              setConfirmReset(true);
            }
          }}
          className={cn(
            'mb-1 ml-auto inline-flex h-6 shrink-0 items-center gap-1.5 rounded-md px-2 text-[11px] transition-colors disabled:opacity-30',
            confirmReset
              ? 'bg-verdict-fail/15 text-verdict-fail'
              : 'text-content-subtle hover:bg-surface-overlay hover:text-content-muted',
          )}
        >
          <RotateCcw aria-hidden className="size-3" />
          {confirmReset ? 'Click again to discard edits' : 'Reset'}
        </button>
      </div>

      <div className="min-h-0 flex-1 bg-surface">
        <MonacoEditor
          path={modelUri}
          loading={<EditorSkeleton />}
          language={languageForPath(path)}
          value={value}
          theme={isDark ? DARK_THEME_ID : LIGHT_THEME_ID}
          beforeMount={(monaco) => {
            monaco.editor.defineTheme(DARK_THEME_ID, DARK_THEME);
            monaco.editor.defineTheme(LIGHT_THEME_ID, LIGHT_THEME);
          }}
          onMount={(editor, monaco) => {
            const mod = monaco.KeyMod.CtrlCmd;
            editor.addCommand(mod | monaco.KeyCode.Enter, () =>
              window.dispatchEvent(new Event(RUN_EVENT)),
            );
            editor.addCommand(mod | monaco.KeyMod.Shift | monaco.KeyCode.Enter, () =>
              window.dispatchEvent(new Event(SUBMIT_EVENT)),
            );
            editor.addCommand(mod | monaco.KeyCode.KeyK, () =>
              window.dispatchEvent(new Event(PALETTE_EVENT)),
            );
            editor.addCommand(mod | monaco.KeyCode.KeyB, () =>
              window.dispatchEvent(new Event(TOGGLE_SIDEBAR_EVENT)),
            );
            editor.onDidChangeCursorPosition((event) =>
              setCursor(event.position.lineNumber, event.position.column),
            );
            // Re-measure once the web font is ready, or the cursor drifts from the glyphs.
            void document.fonts.ready.then(() => monaco.editor.remeasureFonts());
            editor.focus();
          }}
          onChange={(next) => updateFile(path, next ?? '')}
          options={{
            fontSize: 14,
            lineHeight: 22,
            fontFamily: EDITOR_FONT,
            fontLigatures: true,
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            smoothScrolling: false,
            cursorBlinking: 'smooth',
            cursorSmoothCaretAnimation: 'off',
            renderLineHighlight: 'line',
            lineNumbersMinChars: 3,
            glyphMargin: false,
            folding: true,
            tabSize: 4,
            wordWrap: 'off',
            automaticLayout: true,
            padding: { top: 14, bottom: 14 },
            bracketPairColorization: { enabled: true },
            guides: { bracketPairs: false, indentation: true },
            overviewRulerBorder: false,
            hideCursorInOverviewRuler: true,
            scrollbar: {
              verticalScrollbarSize: 10,
              horizontalScrollbarSize: 10,
              alwaysConsumeMouseWheel: false,
            },
          }}
        />
      </div>
    </div>
  );
}
