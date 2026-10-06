'use client';

/**
 * Three-region IDE shell: files and task on the left, editor in the middle,
 * output and results bottom-right.
 *
 * Layout is a CSS grid whose track sizes come from React state, with splitter
 * handles between the tracks. Two decisions worth stating:
 *
 *  - The splitters are `role="separator"` widgets with `aria-valuenow`, arrow-key
 *    adjustment, and a visible focus ring, not bare draggable divs. A layout
 *    control that only responds to a mouse is unusable for a keyboard user and
 *    is invisible to a screen reader entirely.
 *  - Below the `lg` breakpoint the same three regions stack vertically and the
 *    splitters disappear. A 3-column IDE on a phone is unusable, and resizing
 *    panels on a touch viewport mostly fights the browser's own gestures.
 */
import { useCallback, useState } from 'react';
import * as Tabs from '@radix-ui/react-tabs';
import { GripVertical } from 'lucide-react';
import type {
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent,
  ReactNode,
} from 'react';

import { cn } from '@/lib/cn';
import { Editor } from '@/components/ide/Editor';
import { FileExplorer } from '@/components/ide/FileExplorer';
import { Terminal } from '@/components/ide/Terminal';
import { TestResults } from '@/components/ide/TestResults';
import type { ChallengeDetail } from '@/types/api';

const GUTTER = 6;

const LEFT_DEFAULT = 264;
const LEFT_MIN = 180;
const LEFT_MAX = 560;

const BOTTOM_DEFAULT = 260;
const BOTTOM_MIN = 120;
const BOTTOM_MAX = 720;

/** Pixels per arrow-key press on a splitter. */
const KEY_STEP = 16;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export interface IdeLayoutProps {
  readonly challenge: ChallengeDetail;
  /**
   * Run and Submit controls plus the elapsed timer.
   *
   * Injected rather than rendered here because the mode is the page's decision:
   * the page owns the submission request and must not have its buttons hidden
   * inside a layout component.
   */
  readonly toolbar: ReactNode;
}

export function IdeLayout({ challenge, toolbar }: IdeLayoutProps): JSX.Element {
  const [leftWidth, setLeftWidth] = useState(LEFT_DEFAULT);
  const [bottomHeight, setBottomHeight] = useState(BOTTOM_DEFAULT);

  return (
    <div className="flex size-full min-h-0 flex-col bg-surface">
      <div className="flex min-h-0 flex-1 flex-col lg:grid" style={{
        gridTemplateColumns: `${leftWidth}px ${GUTTER}px minmax(0, 1fr)`,
        gridTemplateRows: 'minmax(0, 1fr)',
      }}>
        <aside
          aria-label="Files and task"
          className={cn(
            'flex min-h-0 flex-col border-edge lg:col-start-1 lg:row-start-1 lg:border-r',
            'max-h-60 w-full shrink-0 lg:max-h-none',
          )}
        >
          <Tabs.Root defaultValue="files" className="flex min-h-0 flex-1 flex-col">
            <Tabs.List
              aria-label="Left panel"
              className="flex shrink-0 gap-1 border-b border-edge px-2 py-1.5"
            >
              <TabTrigger value="files">Files</TabTrigger>
              <TabTrigger value="task">Task</TabTrigger>
            </Tabs.List>

            <Tabs.Content
              value="files"
              forceMount
              className="scrollbar-thin min-h-0 flex-1 overflow-auto py-1 outline-none"
            >
              <FileExplorer />
            </Tabs.Content>

            <Tabs.Content
              value="task"
              forceMount
              className="scrollbar-thin min-h-0 flex-1 overflow-auto px-3 py-3 outline-none"
            >
              <TaskPanel challenge={challenge} />
            </Tabs.Content>
          </Tabs.Root>
        </aside>

        <Splitter
          orientation="vertical"
          label="Resize the files panel"
          value={leftWidth}
          min={LEFT_MIN}
          max={LEFT_MAX}
          onChange={setLeftWidth}
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
            {toolbar}
            {/* The editor sizes from this box, not from a magic offset, so a
                taller toolbar cannot push it out of the panel. */}
            <div className="min-h-0 flex-1">
              <Editor scope={challenge.slug} />
            </div>
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
            className="flex h-64 min-h-0 shrink-0 flex-col border-edge lg:row-start-3 lg:h-auto lg:border-t"
          >
            <Tabs.Root defaultValue="output" className="flex min-h-0 flex-1 flex-col">
              <Tabs.List
                aria-label="Run output and test results"
                className="flex shrink-0 gap-1 border-b border-edge px-2 py-1.5"
              >
                <TabTrigger value="output">Output</TabTrigger>
                <TabTrigger value="tests">Tests</TabTrigger>
              </Tabs.List>
              <Tabs.Content
                value="output"
                forceMount
                className="min-h-0 flex-1 overflow-hidden outline-none"
              >
                <Terminal />
              </Tabs.Content>
              <Tabs.Content
                value="tests"
                forceMount
                className="min-h-0 flex-1 overflow-hidden outline-none"
              >
                <TestResults />
              </Tabs.Content>
            </Tabs.Root>
          </section>
        </div>
      </div>
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
        'rounded px-2 py-1 text-xs font-medium text-content-muted transition-colors motion-reduce:transition-none',
        'hover:bg-surface-sunken hover:text-content',
        'data-[state=active]:bg-surface-sunken data-[state=active]:text-content',
      )}
    >
      {children}
    </Tabs.Trigger>
  );
}

/** Task description and metadata. The prompt is plain text, never HTML. */
function TaskPanel({ challenge }: { readonly challenge: ChallengeDetail }): JSX.Element {
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-content-subtle">Language</dt>
        <dd className="font-mono">{challenge.language}</dd>
        <dt className="text-content-subtle">Difficulty</dt>
        <dd className="capitalize">{challenge.difficulty}</dd>
        <dt className="text-content-subtle">Estimate</dt>
        <dd>{challenge.estimated_minutes} min</dd>
      </dl>

      {challenge.tags.length > 0 ? (
        <ul className="flex flex-wrap gap-1">
          {challenge.tags.map((tag) => (
            <li
              key={tag}
              className="rounded-full border border-edge bg-surface-sunken px-2 py-0.5 text-xs text-content-muted"
            >
              {tag}
            </li>
          ))}
        </ul>
      ) : null}

      {/*
        The prompt is rendered as text with whitespace preserved, not as markup.
        It arrives as a string and this app has no HTML sanitiser, so the only
        defensible treatment is to let React escape it verbatim.
      */}
      <div className="whitespace-pre-wrap break-words text-sm leading-relaxed text-content-muted">
        {challenge.prompt}
      </div>
    </div>
  );
}

interface SplitterProps {
  readonly orientation: 'vertical' | 'horizontal';
  readonly label: string;
  readonly value: number;
  readonly min: number;
  readonly max: number;
  readonly onChange: (next: number) => void;
}

function Splitter({ orientation, label, value, min, max, onChange }: SplitterProps): JSX.Element {
  const isVertical = orientation === 'vertical';

  const nudge = useCallback(
    (delta: number) => {
      onChange(clamp(value + delta, min, max));
    },
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
      // Ignore secondary buttons: a right-click drag should open a menu, not
      // resize a panel.
      if (event.button !== 0) {
        return;
      }
      const handle = event.currentTarget;
      handle.setPointerCapture(event.pointerId);

      const move = (moveEvent: PointerEvent): void => {
        const position = isVertical ? moveEvent.clientX : moveEvent.clientY;
        onChange(clamp(position, min, max));
      };
      const end = (): void => {
        if (handle.hasPointerCapture(event.pointerId)) {
          handle.releasePointerCapture(event.pointerId);
        }
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', end);
        window.removeEventListener('pointercancel', end);
        document.body.style.removeProperty('cursor');
        document.body.style.removeProperty('user-select');
      };

      /*
       * Suppressing selection and pinning the resize cursor on the body keeps the
       * drag consistent when the pointer outruns the 6px handle. Without it the
       * drag selects the text underneath and the cursor flickers back to a text
       * caret, which reads as the handle having stopped working.
       */
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
      tabIndex={0}
      onKeyDown={onKeyDown}
      onPointerDown={onPointerDown}
      className={cn(
        'hidden shrink-0 items-center justify-center bg-surface-sunken transition-colors motion-reduce:transition-none lg:flex',
        'hover:bg-edge',
        // Explicit placement. Sibling regions pin their own starts, and sparse
        // auto-placement would otherwise drop this handle into an occupied cell.
        isVertical ? 'w-1.5 cursor-col-resize lg:col-start-2 lg:row-start-1' : 'h-1.5 cursor-row-resize lg:row-start-2',
      )}
    >
      <GripVertical
        aria-hidden
        className={cn('size-3 text-content-subtle', !isVertical && 'rotate-90')}
      />
    </div>
  );
}
