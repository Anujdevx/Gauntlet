import { describe, expect, it } from 'vitest';

import { matchScore } from './skill-match';

describe('matchScore', () => {
  const skill = 'dsa.arrays.prefix_sums';

  it('ranks exact > more specific > closer ancestor > distant ancestor > unrelated', () => {
    const exact = matchScore(skill, 'dsa.arrays.prefix_sums');
    const specific = matchScore(skill, 'dsa.arrays.prefix_sums.range');
    const parent = matchScore(skill, 'dsa.arrays');
    const root = matchScore(skill, 'dsa');
    const unrelated = matchScore(skill, 'dsa.graphs.traversal');
    expect(exact).toBeGreaterThan(specific);
    expect(specific).toBeGreaterThan(parent);
    expect(parent).toBeGreaterThan(root);
    expect(root).toBeGreaterThan(unrelated);
    expect(unrelated).toBe(0);
  });

  it('does not treat a shared prefix string as ancestry', () => {
    expect(matchScore('dsa.arrays', 'dsa.arrays_extra')).toBe(0);
  });
});
