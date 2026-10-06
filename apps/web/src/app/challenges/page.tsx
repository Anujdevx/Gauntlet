'use client';

/**
 * Challenge catalogue.
 *
 * Deliberately renders only what the list endpoint returns: no prompts, no
 * starter files. Those are fetched on the detail route, because a catalogue of
 * forty challenges would otherwise ship hundreds of kilobytes of markdown and
 * code the visitor never looks at.
 */
import Link from 'next/link';
import { useMemo } from 'react';
import { ArrowRight, ServerCrash } from 'lucide-react';

import { AppShell } from '@/components/layout/AppShell';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardContent } from '@/components/ui/Card';
import { Spinner } from '@/components/ui/Spinner';
import { errorMessage, fetchChallenges, isApiError } from '@/lib/api';
import { sanitizeSingleLine } from '@/lib/format';
import { useResource } from '@/hooks/useResource';
import type { Challenge, ChallengeLanguage } from '@/types/api';

const LANGUAGE_LABELS: Readonly<Record<ChallengeLanguage, string>> = {
  python: 'Python',
  react: 'React',
  sql: 'SQL',
};

const DIFFICULTY_TONES = {
  easy: 'pass',
  medium: 'skip',
  hard: 'fail',
} as const;

export default function ChallengesPage(): JSX.Element {
  return (
    <AppShell>
      <div className="mx-auto w-full max-w-4xl px-4 py-8">
        <header className="mb-6">
          <h1 className="text-xl font-semibold tracking-tight">Challenges</h1>
          <p className="mt-1 text-sm text-content-muted">
            Graded only by tests you cannot see. Read the task, write the code, find out.
          </p>
        </header>
        <ChallengeList />
      </div>
    </AppShell>
  );
}

function ChallengeList(): JSX.Element {
  const { state, data, error, reload } = useResource((signal) => fetchChallenges(signal), []);

  const challenges = useMemo(
    () => [...(data?.challenges ?? [])].sort((left, right) => (left.title < right.title ? -1 : left.title > right.title ? 1 : 0)),
    [data],
  );

  if (state === 'loading') {
    return (
      <div className="flex justify-center py-16">
        <Spinner label="Loading challenges" />
      </div>
    );
  }

  if (state === 'error') {
    const offline = isApiError(error) && error.isOffline;
    return (
      <Card>
        <CardContent className="flex flex-col items-start gap-3 py-8">
          <ServerCrash aria-hidden className="size-6 text-verdict-infra" />
          <div>
            <h2 className="text-sm font-semibold">
              {offline ? 'Cannot reach the challenge service' : 'Could not load challenges'}
            </h2>
            <p className="mt-1 text-sm text-content-muted">
              {errorMessage(error, 'The request failed unexpectedly.')}
            </p>
          </div>
          <Button size="sm" onClick={reload}>
            Try again
          </Button>
        </CardContent>
      </Card>
    );
  }

  if (challenges.length === 0) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-content-muted">
          No challenges have been published yet.
        </CardContent>
      </Card>
    );
  }

  return (
    <ul className="space-y-3">
      {challenges.map((challenge) => (
        <li key={challenge.id}>
          <ChallengeCard challenge={challenge} />
        </li>
      ))}
    </ul>
  );
}

function ChallengeCard({ challenge }: { readonly challenge: Challenge }): JSX.Element {
  return (
    <Card className="transition-colors motion-reduce:transition-none hover:border-content-subtle">
      <CardContent className="flex items-start gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold">{sanitizeSingleLine(challenge.title, 120)}</h2>
            <Badge>{LANGUAGE_LABELS[challenge.language]}</Badge>
            <Badge tone={DIFFICULTY_TONES[challenge.difficulty]} className="capitalize">
              {challenge.difficulty}
            </Badge>
          </div>
          <p className="mt-1.5 text-sm text-content-muted">
            {sanitizeSingleLine(challenge.summary, 240)}
          </p>
          <p className="mt-2 text-xs text-content-subtle">
            About {challenge.estimated_minutes} min
            {challenge.concept_nodes.length > 0
              ? ` · Targets ${challenge.concept_nodes
                  .slice(0, 3)
                  .map((node) => sanitizeSingleLine(node, 60))
                  .join(', ')}`
              : ''}
          </p>
        </div>

        {/*
          A stretched link: the whole card is the hit target while the accessible
          name stays the challenge title. Keeps one tab stop per challenge instead
          of adding an unreachable "read more" target.
        */}
        <Link
          href={`/challenges/${encodeURIComponent(challenge.slug)}`}
          className="group relative flex shrink-0 items-center gap-1 self-center rounded px-2 py-1 text-sm text-content-muted hover:bg-surface-sunken hover:text-content"
        >
          <span className="absolute inset-0" aria-hidden />
          <span className="sr-only">Open {sanitizeSingleLine(challenge.title, 120)}</span>
          <ArrowRight
            aria-hidden
            className="size-4 transition-transform motion-reduce:transition-none group-hover:translate-x-0.5"
          />
        </Link>
      </CardContent>
    </Card>
  );
}
