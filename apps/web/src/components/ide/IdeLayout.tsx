'use client';

/**
 * IDE shell: task and files on the left, editor in the middle, output and results
 * below, and a status bar along the bottom.
 *
 * Layout is a CSS grid whose track sizes come from React state, with splitter
 * handles between the tracks. Decisions worth stating:
 *
 *  - The splitters are `role="separator"` widgets with `aria-valuenow`, arrow-key
 *    adjustment, and a visible focus ring, so layout is usable without a mouse.
 *  - Below the `lg` breakpoint the regions stack and the splitters disappear.
 *  - The bottom panel follows the run: it switches to Output when a run starts
 *    and to Tests when a verdict arrives, so the candidate never has to hunt.
 *  - ⌘B collapses the left panel for a distraction-free editor.
 */
import { useCallback, useEffect, useState } from 'react';
import * as Tabs from '@radix-ui/react-tabs';
import { Check, Circle, Loader2, TriangleAlert } from 'lucide-react';
import type {
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent,
  ReactNode,
} from 'react';

import { cn } from '@/lib/cn';
import { LANGUAGE_META } from '@/lib/challenge-meta';
import { formatDuration } from '@/lib/format';
import { TOGGLE_SIDEBAR_EVENT } from '@/lib/shortcuts';
import { Editor, languageForPath } from '@/components/ide/Editor';
import { FileExplorer } from '@/components/ide/FileExplorer';
import { Prose } from '@/components/ide/Prose';
import { Terminal } from '@/components/ide/Terminal';
import { TestResults } from '@/components/ide/TestResults';
import { useIdeStore } from '@/store/ide';
import type { ChallengeDetail } from '@/types/api';

const GUTTER = 5;

const LEFT_DEFAULT = 340;
const LEFT_MIN = 220;
const LEFT_MAX = 620;

const BOTTOM_DEFAULT = 280;
const BOTTOM_MIN = 120;
const BOTTOM_MAX = 720;

/** Pixels per arrow-key press on a splitter. */
const KEY_STEP = 16;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export interface IdeLayoutProps {
  readonly challenge: ChallengeDetail;
  /** Run and Submit controls. Injected: the page owns the submission lifecycle. */
  readonly toolbar: ReactNode;
}

export function IdeLayout({ challenge, toolbar }: IdeLayoutProps): JSX.Element {
  const [leftWidth, setLeftWidth] = useState(LEFT_DEFAULT);
  const [bottomHeight, setBottomHeight] = useState(BOTTOM_DEFAULT);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [bottomTab, setBottomTab] = useState<'output' | 'tests'>('tests');

  const connection = useIdeStore((state) => state.connection);
  const result = useIdeStore((state) => state.result);
  const files = useIdeStore((state) => state.files);

  useEffect(() => {
    const toggle = (): void => setSidebarOpen((open) => !open);
    window.addEventListener(TOGGLE_SIDEBAR_EVENT, toggle);
    return () => window.removeEventListener(TOGGLE_SIDEBAR_EVENT, toggle);
  }, []);

  // Follow the run: Output while it streams, Tests once there is a verdict.
  useEffect(() => {
    if (connection === 'connecting' || connection === 'streaming') setBottomTab('output');
  }, [connection]);
  useEffect(() => {
    // A run with no graded tests has nothing to show on Tests; its output is the point.
    if (result !== null) setBottomTab(result.tests.length === 0 ? 'output' : 'tests');
  }, [result]);

  const testSummary =
    result === null || result.tests.length === 0
      ? null
      : `${result.tests.filter((t) => t.passed).length}/${result.tests.length}`;

  return (
    <div className="flex size-full min-h-0 flex-col bg-surface">
      {toolbar}

      <div
        className="flex min-h-0 flex-1 flex-col lg:grid"
        style={{
          gridTemplateColumns: sidebarOpen
            ? `${leftWidth}px ${GUTTER}px minmax(0, 1fr)`
            : `0px 0px minmax(0, 1fr)`,
          gridTemplateRows: 'minmax(0, 1fr)',
        }}
      >
        <aside
          aria-label="Task and files"
          aria-hidden={!sidebarOpen}
          className={cn(
            'flex min-h-0 flex-col overflow-hidden border-edge bg-surface-raised lg:col-start-1 lg:row-start-1 lg:border-r',
            'max-h-72 w-full shrink-0 lg:max-h-none',
            !sidebarOpen && 'max-lg:hidden lg:invisible lg:border-r-0',
          )}
        >
          <Tabs.Root defaultValue="task" className="flex min-h-0 flex-1 flex-col">
            <Tabs.List
              aria-label="Left panel"
              className="flex shrink-0 gap-1 border-b border-edge px-3 pt-2"
            >
              <TabTrigger value="task">Task</TabTrigger>
              <TabTrigger value="files">Files</TabTrigger>
            </Tabs.List>

            <Tabs.Content
              value="task"
              className="scrollbar-thin min-h-0 flex-1 overflow-auto px-5 py-5 outline-none"
            >
              <TaskPanel challenge={challenge} />
            </Tabs.Content>

            <Tabs.Content
              value="files"
              className="scrollbar-thin min-h-0 flex-1 overflow-auto py-2 outline-none"
            >
              <FileExplorer />
            </Tabs.Content>
          </Tabs.Root>
        </aside>

        <Splitter
          orientation="vertical"
          label="Resize the task panel"
          value={leftWidth}
          min={LEFT_MIN}
          max={LEFT_MAX}
          onChange={setLeftWidth}
          hidden={!sidebarOpen}
        />

        <div
          className="flex min-h-0 min-w-0 flex-1 flex-col lg:col-start-3 lg:row-start-1 lg:grid"
          style={{
            gridTemplateRows: `minmax(0, 1fr) ${GUTTER}px minmax(0, ${bottomHeight}px)`,
          }}
        >
          <section
            aria-label="Editor"
            className="flex min-h-72 min-w-0 flex-1 flex-col overflow-hidden lg:row-start-1"
          >
            <Editor scope={challenge.slug} />
          </section>

          <Splitter
            orientation="horizontal"
            label="Resize the output panel"
            value={bottomHeight}
            min={BOTTOM_MIN}
            max={BOTTOM_MAX}
            onChange={setBottomHeight}
          />

          <section
            aria-label="Output and results"
            className="flex h-72 min-h-0 shrink-0 flex-col bg-surface-raised lg:row-start-3 lg:h-auto"
          >
            <Tabs.Root
              value={bottomTab}
              onValueChange={(value) => setBottomTab(value as 'output' | 'tests')}
              className="flex min-h-0 flex-1 flex-col"
            >
              <Tabs.List
                aria-label="Run output and test results"
                className="flex shrink-0 gap-1 border-b border-edge px-3 pt-2"
              >
                <TabTrigger value="tests">
                  Tests
                  {testSummary !== null ? (
                    <span
                      className={cn(
                        'ml-1.5 rounded-full px-1.5 py-px font-mono text-[10px]',
                        result?.status === 'passed'
                          ? 'bg-verdict-pass/15 text-verdict-pass'
                          : 'bg-verdict-fail/15 text-verdict-fail',
                      )}
                    >
                      {testSummary}
                    </span>
                  ) : null}
                </TabTrigger>
                <TabTrigger value="output">Output</TabTrigger>
              </Tabs.List>
              <Tabs.Content
                value="tests"
                forceMount
                hidden={bottomTab !== 'tests'}
                className="min-h-0 flex-1 overflow-hidden outline-none"
              >
                <TestResults />
              </Tabs.Content>
              <Tabs.Content
                value="output"
                forceMount
                hidden={bottomTab !== 'output'}
                className="min-h-0 flex-1 overflow-hidden outline-none"
              >
                <Terminal />
              </Tabs.Content>
            </Tabs.Root>
          </section>
        </div>
      </div>

      <StatusBar language={files.length > 0 ? languageForPath(files[0]?.path ?? '') : 'plaintext'} />
    </div>
  );
}

function TabTrigger({
  value,
  children,
}: {
  readonly value: string;
  readonly children: ReactNode;
}): JSX.Element {
  return (
    <Tabs.Trigger
      value={value}
      className={cn(
        'relative -mb-px inline-flex items-center px-3 pb-2.5 pt-1.5 text-xs font-medium text-content-subtle transition-colors motion-reduce:transition-none',
        'hover:text-content',
        'data-[state=active]:text-content',
        "data-[state=active]:after:absolute data-[state=active]:after:inset-x-2 data-[state=active]:after:bottom-0 data-[state=active]:after:h-0.5 data-[state=active]:after:rounded-full data-[state=active]:after:bg-accent data-[state=active]:after:content-['']",
      )}
    >
      {children}
    </Tabs.Trigger>
  );
}

/** Task description and metadata. The prompt is rendered by `Prose`, never as HTML. */
function TaskPanel({ challenge }: { readonly challenge: ChallengeDetail }): JSX.Element {
  const lang = LANGUAGE_META[challenge.language];
  return (
    <div className="space-y-5">
      <div>
        <p className="font-mono text-[11px] uppercase tracking-widest" style={{ color: lang.color }}>
          {lang.label} · {challenge.difficulty} · ~{challenge.estimated_minutes} min
        </p>
        <h2 className="mt-1.5 text-lg font-semibold leading-snug tracking-tight">{challenge.title}</h2>
      </div>

      <Prose source={challenge.prompt} />

      {challenge.concept_nodes.length > 0 ? (
        <div>
          <h3 className="mb-2 text-[11px] font-medium uppercase tracking-wider text-content-subtle">
            Skills tested
          </h3>
          <ul className="flex flex-wrap gap-1.5">
            {challenge.concept_nodes.map((node) => (
              <li
                key={node}
                className="rounded-md bg-surface-sunken px-2 py-1 font-mono text-[11px] text-content-muted ring-1 ring-edge"
              >
                {node}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="rounded-lg border border-edge bg-surface-sunken p-3 text-xs leading-relaxed text-content-subtle">
        <p>
          <strong className="font-medium text-content-muted">Run</strong> executes your code so you
          can print and debug; it does not grade. <strong className="font-medium text-content-muted">Submit</strong>{' '}
          runs the hidden tests and records the attempt.
        </p>
      </div>
    </div>
  );
}

/** VS Code-style status strip: run state on the left, editor facts on the right. */
function StatusBar({ language }: { readonly language: string }): JSX.Element {
  const connection = useIdeStore((state) => state.connection);
  const result = useIdeStore((state) => state.result);
  const cursor = useIdeStore((state) => state.cursor);
  const activePath = useIdeStore((state) => state.activePath);

  const busy = connection === 'connecting' || connection === 'streaming' || connection === 'polling';
  let state: { label: string; icon: ReactNode; className: string };
  if (busy) {
    state = {
      label: connection === 'connecting' ? 'Queueing…' : 'Running…',
      icon: <Loader2 aria-hidden className="size-3 animate-spin motion-reduce:animate-none" />,
      className: 'text-accent',
    };
  } else if (result !== null && result.status === 'passed' && result.score.total === 0) {
    state = { label: 'Ran · not graded', icon: <Check aria-hidden className="size-3" />, className: 'text-content-muted' };
  } else if (result !== null && result.status === 'passed') {
    state = { label: `Passed in ${formatDuration(result.runtime_ms)}`, icon: <Check aria-hidden className="size-3" />, className: 'text-verdict-pass' };
  } else if (result !== null && !result.counts_against_candidate) {
    state = { label: 'Platform error', icon: <TriangleAlert aria-hidden className="size-3" />, className: 'text-verdict-infra' };
  } else if (result !== null) {
    state = { label: `${result.score.passed}/${result.score.total} passed`, icon: <TriangleAlert aria-hidden className="size-3" />, className: 'text-verdict-fail' };
  } else {
    state = { label: 'Ready', icon: <Circle aria-hidden className="size-2 fill-current" />, className: 'text-content-subtle' };
  }

  return (
    <footer
      aria-label="Status bar"
      className="flex h-7 shrink-0 items-center gap-4 border-t border-edge bg-surface-sunken px-3 font-mono text-[11px] text-content-subtle"
    >
      <span className={cn('inline-flex items-center gap-1.5', state.className)} role="status">
        {state.icon}
        {state.label}
      </span>
      <span className="hidden truncate sm:inline">{activePath}</span>
      <span className="ml-auto tabular-nums">
        Ln {cursor.line}, Col {cursor.column}
      </span>
      <span className="hidden capitalize sm:inline">{language}</span>
      <span className="hidden sm:inline">UTF-8</span>
    </footer>
  );
}

interface SplitterProps {
  readonly orientation: 'vertical' | 'horizontal';
  readonly label: string;
  readonly value: number;
  readonly min: number;
  readonly max: number;
  readonly onChange: (next: number) => void;
  readonly hidden?: boolean;
}

function Splitter({ orientation, label, value, min, max, onChange, hidden }: SplitterProps): JSX.Element {
  const isVertical = orientation === 'vertical';

  const nudge = useCallback(
    (delta: number) => onChange(clamp(value + delta, min, max)),
    [max, min, onChange, value],
  );

  const onKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      const decrease = isVertical ? 'ArrowLeft' : 'ArrowUp';
      const increase = isVertical ? 'ArrowRight' : 'ArrowDown';
      switch (event.key) {
        case decrease:
          event.preventDefault();
          nudge(-KEY_STEP);
          return;
        case increase:
          event.preventDefault();
          nudge(KEY_STEP);
          return;
        case 'PageDown':
          event.preventDefault();
          nudge(-KEY_STEP * 5);
          return;
        case 'PageUp':
          event.preventDefault();
          nudge(KEY_STEP * 5);
          return;
        case 'Home':
          event.preventDefault();
          onChange(min);
          return;
        case 'End':
          event.preventDefault();
          onChange(max);
          return;
        default:
          return;
      }
    },
    [isVertical, max, min, nudge, onChange],
  );

  const onPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (event.button !== 0) return;
      const handle = event.currentTarget;
      handle.setPointerCapture(event.pointerId);
      // Panel sizes are measured from the container's origin, not the viewport's.
      const container = handle.parentElement?.getBoundingClientRect();
      const origin = container === undefined ? 0 : isVertical ? container.left : container.top;

      const move = (moveEvent: PointerEvent): void => {
        const position = isVertical ? moveEvent.clientX : moveEvent.clientY;
        if (isVertical) {
          onChange(clamp(position - origin, min, max));
        } else {
          // The bottom panel grows as the pointer moves up.
          const end = container === undefined ? 0 : container.bottom;
          onChange(clamp(end - position, min, max));
        }
      };
      const end = (): void => {
        if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', end);
        window.removeEventListener('pointercancel', end);
        document.body.style.removeProperty('cursor');
        document.body.style.removeProperty('user-select');
      };

      // Pin the cursor and suppress selection so a fast drag that outruns the
      // handle does not select text or flicker the cursor.
      document.body.style.cursor = isVertical ? 'col-resize' : 'row-resize';
      document.body.style.userSelect = 'none';
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', end);
      window.addEventListener('pointercancel', end);
    },
    [isVertical, max, min, onChange],
  );

  return (
    <div
      role="separator"
      aria-orientation={orientation}
      aria-label={label}
      aria-valuenow={Math.round(value)}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={hidden ? -1 : 0}
      onKeyDown={onKeyDown}
      onPointerDown={onPointerDown}
      className={cn(
        'group relative hidden shrink-0 touch-none bg-edge/60 transition-colors hover:bg-accent/70 focus-visible:bg-accent lg:block',
        isVertical
          ? 'cursor-col-resize lg:col-start-2 lg:row-start-1'
          : 'cursor-row-resize lg:row-start-2',
        hidden && 'lg:hidden',
      )}
    >
      {/* Larger invisible hit area: a 5px target is miserable to grab. */}
      <span
        aria-hidden
        className={cn('absolute', isVertical ? '-inset-x-1.5 inset-y-0' : '-inset-y-1.5 inset-x-0')}
      />
    </div>
  );
}
