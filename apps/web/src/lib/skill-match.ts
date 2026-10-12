/**
 * How well a challenge's concept node matches the skill we want to practise.
 *
 * Exact beats a more specific descendant, which beats an ancestor, and among ancestors
 * the deeper (closer) one wins. Without this ranking every DSA question matched every DSA
 * skill through the shared root `dsa`, and the first one in the list was recommended.
 */
export function matchScore(skillId: string, conceptId: string): number {
  if (conceptId === skillId) return 1000;
  if (conceptId.startsWith(`${skillId}.`)) return 500;
  if (skillId.startsWith(`${conceptId}.`)) return conceptId.length;
  return 0;
}
