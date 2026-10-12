/**
 * Minimal, safe renderer for challenge prompts.
 *
 * Supports headings, paragraphs, `- ` and numbered lists, ``` fences, `inline code` and
 * **bold**; stray LaTeX is flattened to plain text (see `lib/prose-parse.ts`). It
 * builds React elements only, never HTML strings, so a prompt can never inject
 * markup: React escapes every text node. A full markdown library would be a
 * dependency and a sanitiser to maintain for four constructs.
 */
import type { ReactNode } from 'react';

import { parseBlocks } from '@/lib/prose-parse';

export function parseInline(text: string): ReactNode[] {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).map((part, index) => {
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
      return (
        <code
          key={index}
          className="rounded bg-surface-sunken px-1.5 py-0.5 font-mono text-[0.85em] text-accent ring-1 ring-edge"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      return (
        <strong key={index} className="font-semibold text-content">
          {part.slice(2, -2)}
        </strong>
      );
    }
    return part;
  });
}

export function Prose({ source }: { readonly source: string }): JSX.Element {
  return (
    <div className="space-y-3 text-[13.5px] leading-relaxed text-content-muted">
      {parseBlocks(source).map((block, index) => {
        if (block.kind === 'h') {
          return (
            <h3
              key={index}
              className="pt-1 text-[11px] font-semibold uppercase tracking-wider text-content-subtle"
            >
              {parseInline(block.text)}
            </h3>
          );
        }
        if (block.kind === 'p') return <p key={index}>{parseInline(block.text)}</p>;
        if (block.kind === 'ul') {
          return (
            <ul key={index} className="space-y-1.5 pl-1">
              {block.items.map((item, itemIndex) => (
                <li key={itemIndex} className="flex gap-2.5">
                  <span aria-hidden className="mt-2 size-1 shrink-0 rounded-full bg-accent" />
                  <span>{parseInline(item)}</span>
                </li>
              ))}
            </ul>
          );
        }
        return (
          <pre
            key={index}
            className="scrollbar-thin overflow-x-auto rounded-lg border border-edge bg-surface-sunken p-3 font-mono text-xs text-content"
          >
            {block.text}
          </pre>
        );
      })}
    </div>
  );
}
