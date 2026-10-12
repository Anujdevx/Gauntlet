import { describe, expect, it } from 'vitest';

import { flattenMath, parseBlocks, texToText } from './prose-parse';

describe('texToText', () => {
  it('flattens common macros', () => {
    expect(texToText('0 \\le N \\le 100,000')).toBe('0 ≤ N ≤ 100,000');
    expect(texToText('10^{9}')).toBe('10^9');
    expect(texToText('\\text{max\\_diff}')).toBe('max_diff');
    expect(texToText('O(N \\log N)')).toBe('O(N log N)');
  });
});

describe('flattenMath', () => {
  it('turns inline $...$ into a code span and leaves plain dollars alone', () => {
    expect(flattenMath('Length $0 \\le N$ ok')).toBe('Length `0 ≤ N` ok');
    expect(flattenMath('costs 5 dollars')).toBe('costs 5 dollars');
  });
});

describe('parseBlocks', () => {
  it('parses headings, lists, paragraphs and fences', () => {
    const blocks = parseBlocks(
      ['### Schema', '', 'Intro line', 'continues', '', '- one', '- two', '1. first', '', '```py', 'x = 1', '```'].join('\n'),
    );
    expect(blocks.map((b) => b.kind)).toEqual(['h', 'p', 'ul', 'code']);
    expect(blocks[1]).toEqual({ kind: 'p', text: 'Intro line continues' });
    expect(blocks[2]).toEqual({ kind: 'ul', items: ['one', 'two', 'first'] });
    expect(blocks[3]).toEqual({ kind: 'code', text: 'x = 1' });
  });

  it('never produces markup: text stays text', () => {
    const [block] = parseBlocks('<script>alert(1)</script>');
    expect(block).toEqual({ kind: 'p', text: '<script>alert(1)</script>' });
  });
});
