/**
 * Minimal, safe renderer for challenge prompts.
 *
 * Supports paragraphs, `- ` lists, ``` fences, `inline code` and **bold**. It
 * builds React elements only, never HTML strings, so a prompt can never inject
 * markup: React escapes every text node. A full markdown library would be a
 * dependency and a sanitiser to maintain for four constructs.
 */
import type { ReactNode } from 'react';

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

type Block =
  | { readonly kind: 'p'; readonly text: string }
  | { readonly kind: 'ul'; readonly items: readonly string[] }
  | { readonly kind: 'code'; readonly text: string };

export function parseBlocks(source: string): Block[] {
  const blocks: Block[] = [];
  const lines = source.replace(/\r\n/g, '\n').split('\n');
  let paragraph: string[] = [];
  let list: string[] = [];

  const flush = (): void => {
    if (paragraph.length > 0) blocks.push({ kind: 'p', text: paragraph.join(' ') });
    if (list.length > 0) blocks.push({ kind: 'ul', items: list });
    paragraph = [];
    list = [];
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i] ?? '';
    if (line.trimStart().startsWith('```')) {
      flush();
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !(lines[i] ?? '').trimStart().startsWith('```')) {
        body.push(lines[i] ?? '');
        i += 1;
      }
      blocks.push({ kind: 'code', text: body.join('\n') });
    } else if (/^\s*[-*]\s+/.test(line)) {
      if (paragraph.length > 0) {
        blocks.push({ kind: 'p', text: paragraph.join(' ') });
        paragraph = [];
      }
      list.push(line.replace(/^\s*[-*]\s+/, ''));
    } else if (line.trim() === '') {
      flush();
    } else {
      if (list.length > 0) flush();
      paragraph.push(line.trim());
    }
  }
  flush();
  return blocks;
}

export function Prose({ source }: { readonly source: string }): JSX.Element {
  return (
    <div className="space-y-3 text-[13.5px] leading-relaxed text-content-muted">
      {parseBlocks(source).map((block, index) => {
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
