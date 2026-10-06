'use client';

/**
 * Challenge catalogue.
 *
 * Renders only what the list endpoint returns: no prompts, no starter files.
 * Those are fetched on the detail route, because a catalogue of forty challenges
 * would otherwise ship hundreds of kilobytes the visitor never looks at.
 */
import Link from 'next/link';
import { useMemo, useState } from 'react';
import { ArrowRight, Clock, SearchX, ServerCrash } from 'lucide-react';

import { AppShell } from '@/components/layout/AppShell';
import { Button } from '@/components/ui/Button';
import { cn } from '@/lib/cn';
import { errorMessage, fetchChallenges, isApiError } from '@/lib/api';
import { DIFFICULTY_META, LANGUAGE_META } from '@/lib/challenge-meta';
import { sanitizeSingleLine } from '@/lib/format';
import { useResource } from '@/hooks/useResource';
import type { Challenge, ChallengeDifficulty, ChallengeLanguage } from '@/types/api';

const DIFFICULTY_ORDER: readonly ChallengeDifficulty[] = ['easy', 'medium', 'hard'];

export default function ChallengesPage(): JSX.Element {
  return (
    <AppShell>
      <div className="relative">
        <div aria-hidden className="bg-grid absolute inset-x-0 top-0 h-72 opacity-60" />
        <div className="relative mx-auto w-full max-w-6xl px-6 py-12">
          <header className="mb-8 max-w-2xl animate-fade-up">
            <p className="font-mono text-xs uppercase tracking-widest text-accent">Challenges</p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">
              Pick your next fight.
            </h1>
            <p className="mt-3 text-content-muted">
              Graded only by tests you cannot see. Read the task, write the code, find out.
            </p>
          </header>
          <ChallengeList />
        </div>
      </div>
    </AppShell>
  );
}

function ChallengeList(): JSX.Element {
  const { state, data, error, reload } = useResource((signal) => fetchChallenges(signal), []);
  const [query, setQuery] = useState('');
  const [language, setLanguage] = useState<ChallengeLanguage | 'all'>('all');
  const [difficulty, setDifficulty] = useState<ChallengeDifficulty | 'all'>('all');

  const all = useMemo(() => data?.challenges ?? [], [data]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return all
      .filter((c) => language === 'all' || c.language === language)
      .filter((c) => difficulty === 'all' || c.difficulty === difficulty)
      .filter(
        (c) =>
          needle === '' ||
          `${c.title} ${c.summary} ${c.tags.join(' ')} ${c.concept_nodes.join(' ')}`
            .toLowerCase()
            .includes(needle),
      )
      .sort(
        (a, b) =>
          DIFFICULTY_META[a.difficulty].level - DIFFICULTY_META[b.difficulty].level ||
          a.title.localeCompare(b.title),
      );
  }, [all, query, language, difficulty]);

  if (state === 'loading') {
    return (
      <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-label="Loading challenges">
        {[0, 1, 2].map((i) => (
          <li key={i} className="skeleton h-52" />
        ))}
      </ul>
    );
  }

  if (state === 'error') {
    const offline = isApiError(error) && error.isOffline;
    const unauthorized = isApiError(error) && error.isUnauthorized;
    return (
      <div className="rounded-panel border border-edge bg-surface-raised p-8">
        <ServerCrash aria-hidden className="size-7 text-verdict-infra" />
        <h2 className="mt-3 font-semibold">
          {unauthorized
            ? 'Sign in to see the challenges'
            : offline
              ? 'Cannot reach the challenge service'
              : 'Could not load challenges'}
        </h2>
        <p className="mt-1 max-w-md text-sm text-content-muted">
          {unauthorized
            ? 'The question bank is only visible to signed-in candidates.'
            : errorMessage(error, 'The request failed unexpectedly.')}
        </p>
        <div className="mt-4">
          {unauthorized ? (
            <Link
              href="/login?next=/challenges"
              className="inline-flex h-9 items-center rounded-lg bg-accent px-4 text-sm font-medium text-accent-fg shadow-glow-sm"
            >
              Sign in
            </Link>
          ) : (
            <Button size="sm" onClick={reload}>
              Try again
            </Button>
          )}
        </div>
      </div>
    );
  }

  if (all.length === 0) {
    return (
      <p className="rounded-panel border border-edge bg-surface-raised p-10 text-center text-sm text-content-muted">
        No challenges have been published yet.
      </p>
    );
  }

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search by title, skill or topic…"
          aria-label="Search challenges"
          className="h-10 w-full rounded-lg border border-edge bg-surface-raised px-3.5 text-sm placeholder:text-content-subtle hover:border-edge-strong sm:w-72"
        />
        <ChipGroup
          label="Language"
          value={language}
          onChange={setLanguage}
          options={[
            { value: 'all', label: 'All' },
            ...(Object.keys(LANGUAGE_META) as ChallengeLanguage[]).map((value) => ({
              value,
              label: LANGUAGE_META[value].label,
            })),
          ]}
        />
        <ChipGroup
          label="Difficulty"
          value={difficulty}
          onChange={setDifficulty}
          options={[
            { value: 'all', label: 'Any' },
            ...DIFFICULTY_ORDER.map((value) => ({ value, label: value })),
          ]}
        />
        <p className="ml-auto text-xs text-content-subtle" aria-live="polite">
          {visible.length} of {all.length}
        </p>
      </div>

      {visible.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-panel border border-dashed border-edge-strong py-16 text-center">
          <SearchX aria-hidden className="size-6 text-content-subtle" />
          <p className="text-sm text-content-muted">No challenge matches those filters.</p>
          <button
            type="button"
            onClick={() => {
              setQuery('');
              setLanguage('all');
              setDifficulty('all');
            }}
            className="text-sm text-accent underline underline-offset-4"
          >
            Clear filters
          </button>
        </div>
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {visible.map((challenge, index) => (
            <li key={challenge.id} className="animate-fade-up" style={{ animationDelay: `${index * 50}ms` }}>
              <ChallengeCard challenge={challenge} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface ChipGroupProps<T extends string> {
  readonly label: string;
  readonly value: T;
  readonly onChange: (value: T) => void;
  readonly options: readonly { readonly value: T; readonly label: string }[];
}

function ChipGroup<T extends string>({ label, value, onChange, options }: ChipGroupProps<T>): JSX.Element {
  return (
    <div role="group" aria-label={label} className="flex rounded-lg border border-edge bg-surface-raised p-0.5">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
          className={cn(
            'rounded-md px-3 py-1.5 text-xs font-medium capitalize transition-colors',
            value === option.value
              ? 'bg-accent text-accent-fg shadow-glow-sm'
              : 'text-content-muted hover:text-content',
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/** First paragraph of the markdown prompt, with markup characters removed. */
function plainSummary(markdown: string): string {
  const firstBlock = markdown.split(/\n\s*\n/)[0] ?? markdown;
  return sanitizeSingleLine(firstBlock.replace(/^#+\s*/gm, '').replace(/[`*_]/g, ''), 200);
}

function ChallengeCard({ challenge }: { readonly challenge: Challenge }): JSX.Element {
  const lang = LANGUAGE_META[challenge.language];
  const diff = DIFFICULTY_META[challenge.difficulty];
  const topics = challenge.concept_nodes.slice(0, 3);

  return (
    <article className="card-lift group relative flex h-full flex-col rounded-panel border border-edge bg-surface-raised p-5">
      <div className="flex items-start justify-between gap-3">
        <span
          aria-hidden
          className="inline-flex h-10 min-w-10 items-center justify-center rounded-lg px-2 font-mono text-xs font-bold"
          style={{
            color: lang.color,
            backgroundColor: `color-mix(in srgb, ${lang.color} 14%, transparent)`,
            boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${lang.color} 35%, transparent)`,
          }}
        >
          {lang.glyph}
        </span>
        <div className="flex items-center gap-2 text-xs">
          <span className="flex items-end gap-0.5" aria-hidden>
            {[1, 2, 3].map((n) => (
              <span
                key={n}
                className={cn('w-1 rounded-sm', n <= diff.level ? diff.bar : 'bg-edge-strong')}
                style={{ height: `${6 + n * 3}px` }}
              />
            ))}
          </span>
          <span className={cn('font-medium capitalize', diff.text)}>{challenge.difficulty}</span>
        </div>
      </div>

      <h2 className="mt-4 text-[17px] font-semibold leading-snug tracking-tight">
        {sanitizeSingleLine(challenge.title, 100)}
      </h2>
      <p className="mt-2 line-clamp-3 flex-1 text-sm leading-relaxed text-content-muted">
        {plainSummary(challenge.summary)}
      </p>

      {topics.length > 0 ? (
        <ul className="mt-4 flex flex-wrap gap-1.5">
          {topics.map((node) => (
            <li
              key={node}
              className="rounded-md bg-surface-sunken px-2 py-0.5 font-mono text-[11px] text-content-subtle"
            >
              {sanitizeSingleLine(node.split('.').slice(-2).join('.'), 40)}
            </li>
          ))}
        </ul>
      ) : null}

      <div className="mt-4 flex items-center justify-between border-t border-edge pt-3 text-xs text-content-subtle">
        <span className="inline-flex items-center gap-1.5">
          <Clock aria-hidden className="size-3.5" />~{challenge.estimated_minutes} min
        </span>
        <span className="inline-flex items-center gap-1 font-medium text-content-muted transition-colors group-hover:text-accent">
          Start
          <ArrowRight
            aria-hidden
            className="size-3.5 transition-transform group-hover:translate-x-1"
          />
        </span>
      </div>

      {/* Stretched link: the whole card is the hit target; one tab stop per card. */}
      <Link
        href={`/challenges/${encodeURIComponent(challenge.slug)}`}
        className="absolute inset-0 rounded-panel"
      >
        <span className="sr-only">Open {sanitizeSingleLine(challenge.title, 100)}</span>
      </Link>
    </article>
  );
}
