/**
 * Pure parsing for challenge prompts (no React, so it is unit-testable).
 *
 * Prompts are Markdown written by people or by an LLM. LLMs also reach for LaTeX
 * (`$0 \le N \le 10^5$`) even when told not to, and this app has no math renderer, so
 * stray TeX is flattened to readable plain text rather than shown as raw backslashes.
 */

export type Block =
  | { readonly kind: 'h'; readonly level: 1 | 2 | 3; readonly text: string }
  | { readonly kind: 'p'; readonly text: string }
  | { readonly kind: 'ul'; readonly items: readonly string[] }
  | { readonly kind: 'code'; readonly text: string };

const MACROS: ReadonlyArray<readonly [RegExp, string]> = [
  [/\\(?:le|leq)\b/g, '≤'],
  [/\\(?:ge|geq)\b/g, '≥'],
  [/\\(?:ne|neq)\b/g, '≠'],
  [/\\times\b/g, '×'],
  [/\\cdot\b/g, '·'],
  [/\\(?:lt)\b/g, '<'],
  [/\\(?:gt)\b/g, '>'],
  [/\\(?:sum)\b/g, 'Σ'],
  [/\\(?:infty)\b/g, '∞'],
  [/\\(?:log)\b/g, 'log'],
  [/\\(?:text|mathrm|mathbf|operatorname)\{([^}]*)\}/g, '$1'],
  [/\^\{([^}]*)\}/g, '^$1'],
  [/_\{([^}]*)\}/g, '_$1'],
  [/\\_/g, '_'],
  [/\\,|\;|\\!/g, ' '],
];

/** Flatten the TeX inside one `$...$` span to plain text. */
export function texToText(tex: string): string {
  let out = tex;
  for (const [pattern, replacement] of MACROS) out = out.replace(pattern, replacement);
  return out.replace(/\\([A-Za-z]+)/g, '$1').replace(/\s+/g, ' ').trim();
}

/** Replace every inline `$...$` span with its plain-text form. */
export function flattenMath(text: string): string {
  return text.replace(/\$([^$\n]+)\$/g, (_match, tex: string) => `\`${texToText(tex)}\``);
}

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
    const heading = /^(#{1,3})\s+(.*\S)\s*$/.exec(line);
    if (line.trimStart().startsWith('```')) {
      flush();
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !(lines[i] ?? '').trimStart().startsWith('```')) {
        body.push(lines[i] ?? '');
        i += 1;
      }
      blocks.push({ kind: 'code', text: body.join('\n') });
    } else if (heading !== null) {
      flush();
      blocks.push({
        kind: 'h',
        level: (heading[1]?.length ?? 3) as 1 | 2 | 3,
        text: flattenMath(heading[2] ?? ''),
      });
    } else if (/^\s*[-*]\s+/.test(line)) {
      if (paragraph.length > 0) {
        blocks.push({ kind: 'p', text: paragraph.join(' ') });
        paragraph = [];
      }
      list.push(flattenMath(line.replace(/^\s*[-*]\s+/, '')));
    } else if (/^\s*\d+\.\s+/.test(line)) {
      // Numbered lists render as bullets: ordering is rarely load-bearing in a prompt.
      if (paragraph.length > 0) {
        blocks.push({ kind: 'p', text: paragraph.join(' ') });
        paragraph = [];
      }
      list.push(flattenMath(line.replace(/^\s*\d+\.\s+/, '')));
    } else if (line.trim() === '') {
      flush();
    } else {
      if (list.length > 0) flush();
      paragraph.push(flattenMath(line.trim()));
    }
  }
  flush();
  return blocks;
}
