/** Display metadata shared by the catalogue and the IDE toolbar. */
import type { ChallengeDifficulty, ChallengeLanguage } from '@/types/api';

export const LANGUAGE_META: Readonly<
  Record<ChallengeLanguage, { readonly label: string; readonly glyph: string; readonly color: string }>
> = {
  python: { label: 'Python', glyph: 'Py', color: '#5b9bd5' },
  react: { label: 'React', glyph: 'Rx', color: '#4fd1e8' },
  sql: { label: 'SQL', glyph: 'SQL', color: '#e0a64a' },
  java: { label: 'Java', glyph: 'Jv', color: '#e0605e' },
};

export const DIFFICULTY_META: Readonly<
  Record<ChallengeDifficulty, { readonly level: number; readonly bar: string; readonly text: string }>
> = {
  easy: { level: 1, bar: 'bg-verdict-pass', text: 'text-verdict-pass' },
  medium: { level: 2, bar: 'bg-verdict-skip', text: 'text-verdict-skip' },
  hard: { level: 3, bar: 'bg-verdict-fail', text: 'text-verdict-fail' },
};

