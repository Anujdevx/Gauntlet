'use client';

/**
 * File tree for the challenge's starter files.
 *
 * The API sends a flat `Record<path, string>`, so the hierarchy is rebuilt here.
 * Two consequences drive the implementation:
 *
 *  - The tree renders as a *flat* list of rows carrying `aria-level`,
 *    `aria-setsize`, and `aria-posinset`, the ARIA-recommended alternative to
 *    nested `role="group"` elements. Visible rows and navigable rows then cannot
 *    drift apart, which is the usual failure mode of a nested tree that
 *    collapses rows the keyboard handler still walks.
 *  - Navigation follows the WAI-ARIA tree pattern with a roving `tabindex`, so
 *    the whole tree costs one tab stop. Tab-per-file would make a twenty-file
 *    challenge cost twenty stops before the candidate reaches the editor.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronRight, FileCode2, FileText, Folder, FolderOpen } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';

import { cn } from '@/lib/cn';
import { sanitizeSingleLine } from '@/lib/format';
import { useIdeStore } from '@/store/ide';

/** Indentation per depth level, in pixels. Applied inline because it is arithmetic. */
const INDENT_STEP = 12;
const INDENT_BASE = 8;

interface TreeNode {
  readonly name: string;
  readonly path: string;
  readonly kind: 'dir' | 'file';
  readonly children: readonly TreeNode[];
}

/** Mutable shape used while building, converted to the immutable tree below. */
interface BuildNode {
  readonly name: string;
  readonly path: string;
  readonly kind: 'dir' | 'file';
  readonly children: Map<string, BuildNode>;
}

interface VisibleRow {
  readonly path: string;
  readonly name: string;
  /** 1-based, matching `aria-level`. */
  readonly level: number;
  readonly kind: 'dir' | 'file';
  readonly expanded: boolean;
  readonly setSize: number;
  readonly positionInSet: number;
  readonly modified: boolean;
}

/**
 * Rebuild a directory hierarchy from flat paths.
 *
 * A path that collides with a directory name (`src` as both a file and a folder)
 * is not representable. The directory wins, because the alternative is silently
 * dropping a file the candidate would never see and could never edit.
 */
function buildTree(paths: readonly string[]): TreeNode | null {
  const root: BuildNode = { name: '', path: '', kind: 'dir', children: new Map() };

  for (const fullPath of paths) {
    const segments = fullPath.split('/').filter((segment) => segment !== '');
    if (segments.length === 0) {
      continue;
    }
    let cursor = root;
    for (const [index, segment] of segments.entries()) {
      const isLeaf = index === segments.length - 1;
      const path = cursor.path === '' ? segment : `${cursor.path}/${segment}`;
      if (isLeaf) {
        cursor.children.set(segment, {
          name: segment,
          path,
          kind: 'file',
          children: new Map(),
        });
        break;
      }
      const existing = cursor.children.get(segment);
      const next =
        existing === undefined || existing.kind === 'file'
          ? { name: segment, path, kind: 'dir' as const, children: new Map<string, BuildNode>() }
          : existing;
      cursor.children.set(segment, next);
      cursor = next;
    }
  }

  return freeze(root);
}

/**
 * Order children directories-first, then alphabetically.
 *
 * The grouping is what makes a tree scannable; a purely alphabetical tree
 * interleaves folders and their contents with unrelated siblings.
 */
function freeze(node: BuildNode): TreeNode {
  const children = [...node.children.values()]
    .sort((left, right) => {
      if (left.kind !== right.kind) {
        return left.kind === 'dir' ? -1 : 1;
      }
      return left.name < right.name ? -1 : left.name > right.name ? 1 : 0;
    })
    .map(freeze);
  return { name: node.name, path: node.path, kind: node.kind, children };
}

/**
 * Flatten to the rows that should currently be visible.
 *
 * `aria-posinset` and `aria-setsize` are computed per sibling group during the
 * walk, which is the only point at which a row's position within its own level is
 * known. Deriving them afterwards from a flat index would be wrong for every row
 * that is not at the top level.
 */
function visibleRows(
  root: TreeNode | null,
  expanded: ReadonlySet<string>,
  modifiedPaths: ReadonlySet<string>,
): readonly VisibleRow[] {
  if (root === null) {
    return [];
  }
  const rows: VisibleRow[] = [];
  const walk = (node: TreeNode, childLevel: number): void => {
    const setSize = node.children.length;
    for (const [index, child] of node.children.entries()) {
      const childExpanded = child.kind === 'dir' && expanded.has(child.path);
      rows.push({
        path: child.path,
        name: child.name,
        level: childLevel,
        kind: child.kind,
        expanded: childExpanded,
        setSize,
        positionInSet: index + 1,
        modified: child.kind === 'file' && modifiedPaths.has(child.path),
      });
      if (childExpanded) {
        walk(child, childLevel + 1);
      }
    }
  };
  walk(root, 1);
  return rows;
}

export function FileExplorer(): JSX.Element {
  // `paths` is selected separately from `files`: it only changes when the
  // challenge loads, so the tree is not rebuilt on every keystroke, which is
  // what memoising on `files` would have caused.
  const paths = useIdeStore((state) => state.paths);
  const files = useIdeStore((state) => state.files);
  const activePath = useIdeStore((state) => state.activePath);
  const selectFile = useIdeStore((state) => state.selectFile);

  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set<string>());
  const [focusedPath, setFocusedPath] = useState<string | null>(null);
  const nodeRefs = useRef(new Map<string, HTMLButtonElement>());

  const tree = useMemo(() => buildTree(paths), [paths]);
  const modifiedPaths = useMemo(
    () => new Set(files.filter((file) => file.content !== file.pristine).map((file) => file.path)),
    [files],
  );
  const rows = useMemo(() => visibleRows(tree, expanded, modifiedPaths), [
    tree,
    expanded,
    modifiedPaths,
  ]);

  /*
   * Expand the top level once per challenge.
   *
   * A flat challenge must show its files immediately, and one nested a single
   * level deep should reveal what is inside without costing the candidate a
   * keystroke they do not yet know they need. Keyed on the top-level signature
   * rather than on `tree`, which changes identity whenever the file list does.
   */
  const autoExpandedRef = useRef<string | null>(null);
  useEffect(() => {
    if (tree === null) {
      return;
    }
    const signature = tree.children.map((child) => child.path).join('\u0000');
    if (autoExpandedRef.current === signature) {
      return;
    }
    autoExpandedRef.current = signature;
    setExpanded(
      new Set(
        tree.children.filter((child) => child.kind === 'dir').map((child) => child.path),
      ),
    );
  }, [tree]);

  /*
   * Keep the roving cursor on a row that still exists. Collapsing a directory
   * removes rows, and a cursor pointing at a removed row would leave the tree
   * with no tab stop at all and therefore unreachable by keyboard.
   */
  useEffect(() => {
    if (rows.length === 0) {
      setFocusedPath(null);
      return;
    }
    setFocusedPath((current) => {
      if (current !== null && rows.some((row) => row.path === current)) {
        return current;
      }
      const preferred =
        current === null
          ? undefined
          : rows.find((row) => row.path === activePath);
      const first = preferred ?? rows[0];
      return first === undefined ? null : first.path;
    });
  }, [rows, activePath]);

  useEffect(() => {
    if (focusedPath === null) {
      return;
    }
    nodeRefs.current.get(focusedPath)?.focus();
  }, [focusedPath]);

  const toggle = useCallback((path: string, next?: boolean): void => {
    setExpanded((current) => {
      const copy = new Set(current);
      const shouldExpand = next ?? !copy.has(path);
      if (shouldExpand) {
        copy.add(path);
      } else {
        copy.delete(path);
      }
      return copy;
    });
  }, []);

  const indexOfRow = useCallback(
    (path: string): number => rows.findIndex((row) => row.path === path),
    [rows],
  );

  const focusRow = useCallback(
    (index: number): void => {
      if (rows.length === 0) {
        return;
      }
      const row = rows[Math.min(Math.max(index, 0), rows.length - 1)];
      if (row !== undefined) {
        setFocusedPath(row.path);
      }
    },
    [rows],
  );

  const onKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLButtonElement>, row: VisibleRow): void => {
      const index = indexOfRow(row.path);
      switch (event.key) {
        case 'ArrowDown':
          event.preventDefault();
          focusRow(index + 1);
          return;
        case 'ArrowUp':
          event.preventDefault();
          focusRow(index - 1);
          return;
        case 'ArrowRight':
          // Expand a collapsed directory; step into an already-expanded one.
          event.preventDefault();
          if (row.kind === 'dir' && !row.expanded) {
            toggle(row.path, true);
          } else if (row.kind === 'dir' && row.expanded) {
            focusRow(index + 1);
          }
          return;
        case 'ArrowLeft': {
          event.preventDefault();
          if (row.kind === 'dir' && row.expanded) {
            toggle(row.path, false);
            return;
          }
          // Otherwise step out to the containing directory.
          const separator = row.path.lastIndexOf('/');
          if (separator === -1) {
            return;
          }
          const parentIndex = indexOfRow(row.path.slice(0, separator));
          if (parentIndex !== -1) {
            focusRow(parentIndex);
          }
          return;
        }
        case 'Home':
          event.preventDefault();
          focusRow(0);
          return;
        case 'End':
          event.preventDefault();
          focusRow(rows.length - 1);
          return;
        case 'Enter':
        case ' ':
          event.preventDefault();
          if (row.kind === 'dir') {
            toggle(row.path);
          } else {
            selectFile(row.path);
          }
          return;
        default:
          return;
      }
    },
    [focusRow, indexOfRow, rows, selectFile, toggle],
  );

  if (rows.length === 0) {
    return <p className="px-3 py-2 text-xs text-content-subtle">No files for this challenge.</p>;
  }

  return (
    <div role="tree" aria-label="Challenge files" className="py-1 text-sm">
      {rows.map((row) => {
        const Icon = rowIcon(row);
        const isActive = row.kind === 'file' && row.path === activePath;
        return (
          <button
            key={row.path}
            type="button"
            role="treeitem"
            ref={(element: HTMLButtonElement | null) => {
              if (element === null) {
                nodeRefs.current.delete(row.path);
              } else {
                nodeRefs.current.set(row.path, element);
              }
            }}
            aria-selected={isActive}
            {...(row.kind === 'dir' ? { 'aria-expanded': row.expanded } : {})}
            aria-level={row.level}
            aria-setsize={row.setSize}
            aria-posinset={row.positionInSet}
            tabIndex={row.path === focusedPath ? 0 : -1}
            onFocus={() => setFocusedPath(row.path)}
            onClick={() => {
              setFocusedPath(row.path);
              if (row.kind === 'dir') {
                toggle(row.path);
              } else {
                selectFile(row.path);
              }
            }}
            onKeyDown={(event) => onKeyDown(event, row)}
            style={{ paddingLeft: `${INDENT_BASE + (row.level - 1) * INDENT_STEP}px` }}
            className={cn(
              'flex w-full items-center gap-1.5 py-1 pr-2 text-left transition-colors motion-reduce:transition-none',
              isActive
                ? 'bg-surface-sunken font-medium text-content'
                : 'text-content-muted hover:bg-surface-sunken hover:text-content',
            )}
          >
            {row.kind === 'dir' ? (
              <ChevronRight
                aria-hidden
                className={cn(
                  'size-3.5 shrink-0 transition-transform motion-reduce:transition-none',
                  row.expanded && 'rotate-90',
                )}
              />
            ) : (
              <span aria-hidden className="size-3.5 shrink-0" />
            )}
            <Icon aria-hidden className="size-3.5 shrink-0 opacity-70" />
            <span className="truncate">{sanitizeSingleLine(row.name, 80)}</span>
            {row.modified ? (
              <>
                {/*
                  The dot itself is hidden from assistive technology because the
                  row's accessible name already says "modified". A bare unsaved-changes
                  glyph tells a screen-reader user nothing about which file it refers to.
                */}
                <span
                  aria-hidden
                  title="Modified"
                  className="ml-auto size-1.5 shrink-0 rounded-full bg-content-muted"
                />
                <span className="sr-only"> (modified)</span>
              </>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

function rowIcon(row: VisibleRow): LucideIcon {
  if (row.kind === 'dir') {
    return row.expanded ? FolderOpen : Folder;
  }
  return /\.(md|mdx|txt|rst)$/i.test(row.name) ? FileText : FileCode2;
}
