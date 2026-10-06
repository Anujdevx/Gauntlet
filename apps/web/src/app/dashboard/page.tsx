'use client';

/**
 * Skill matrix: mastery across the concept graph, grouped by domain.
 *
 * A Bayesian mastery posterior of 0 is ambiguous: zero attempts and zero skill
 * look identical in the data, and rendering both as a red 0% bar would tell a
 * candidate they are weak at something they have never been asked. Unseen nodes
 * are therefore rendered distinctly, and every bar is paired with its numeric
 * value so colour is never the only signal.
 */
import Link from 'next/link';
import { useMemo } from 'react';
import { ArrowRight, Compass, Flame, Layers, ServerCrash, Sparkles, TriangleAlert } from 'lucide-react';
import type { ReactNode } from 'react';

import { AppShell } from '@/components/layout/AppShell';
import { Button } from '@/components/ui/Button';
import { ProgressRing } from '@/components/ui/ProgressRing';
import { cn } from '@/lib/cn';
import { errorMessage, fetchChallenges, fetchSkills, isApiError } from '@/lib/api';
import {
  formatMastery,
  formatRelativeTime,
  masteryBand,
  masteryBarClass,
  masteryToneClass,
  sanitizeSingleLine,
} from '@/lib/format';
import { useResource } from '@/hooks/useResource';
import type { Challenge, SkillNode } from '@/types/api';

export default function DashboardPage(): JSX.Element {
  return (
    <AppShell>
      <div className="relative">
        <div aria-hidden className="bg-grid absolute inset-x-0 top-0 h-72 opacity-60" />
        <div className="relative mx-auto w-full max-w-6xl px-6 py-12">
          <header className="mb-8 max-w-2xl animate-fade-up">
            <p className="font-mono text-xs uppercase tracking-widest text-accent">Concept DNA</p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">Your skill map</h1>
            <p className="mt-3 text-content-muted">
              What the model believes you have demonstrated, and where the evidence is thin.
            </p>
          </header>
          <SkillMatrix />
        </div>
      </div>
    </AppShell>
  );
}

/* -------------------------------------------------------------------------- */
/* Derived data                                                                */
/* -------------------------------------------------------------------------- */

interface DomainGroup {
  readonly domain: string;
  readonly nodes: readonly SkillNode[];
  readonly seen: number;
  /** Mean mastery over attempted nodes, or null when none were attempted. */
  readonly average: number | null;
}

/** Short domain ids read as acronyms (SQL, RAG); longer ones as words. */
function domainLabel(domain: string): string {
  return domain.length <= 3 ? domain.toUpperCase() : domain.charAt(0).toUpperCase() + domain.slice(1);
}

function groupByDomain(nodes: readonly SkillNode[]): readonly DomainGroup[] {
  const byDomain = new Map<string, SkillNode[]>();
  for (const node of nodes) {
    const bucket = byDomain.get(node.domain);
    if (bucket === undefined) byDomain.set(node.domain, [node]);
    else bucket.push(node);
  }
  return [...byDomain.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([domain, entries]) => {
      const ordered = [...entries].sort((a, b) => a.depth - b.depth || a.id.localeCompare(b.id));
      const attempted = ordered.filter((node) => node.attempts > 0);
      return {
        domain,
        nodes: ordered,
        seen: attempted.length,
        average:
          attempted.length === 0
            ? null
            : attempted.reduce((sum, node) => sum + node.mastery, 0) / attempted.length,
      };
    });
}

/**
 * The node to practise next: the weakest attempted leaf below "strong", else the
 * first leaf never attempted. Leaves only, because a challenge targets a concrete
 * skill, not a category heading.
 */
function pickFocus(nodes: readonly SkillNode[]): SkillNode | null {
  const parents = new Set(nodes.map((node) => node.parent_id).filter((id): id is string => id !== null));
  const leaves = nodes.filter((node) => !parents.has(node.id));
  const weak = leaves
    .filter((node) => node.attempts > 0 && node.mastery < 0.7)
    .sort((a, b) => a.mastery - b.mastery)[0];
  return weak ?? leaves.find((node) => node.attempts === 0) ?? null;
}

function challengeFor(node: SkillNode, challenges: readonly Challenge[]): Challenge | null {
  const targets = (c: Challenge): boolean =>
    c.concept_nodes.some((id) => id === node.id || id.startsWith(`${node.id}.`) || node.id.startsWith(`${id}.`));
  return (
    challenges.find(targets) ??
    challenges.find((c) => c.concept_nodes.some((id) => id.split('.')[0] === node.domain)) ??
    null
  );
}

/* -------------------------------------------------------------------------- */
/* Page body                                                                   */
/* -------------------------------------------------------------------------- */

function SkillMatrix(): JSX.Element {
  const skills = useResource((signal) => fetchSkills(signal), []);
  const catalogue = useResource((signal) => fetchChallenges(signal), []);
  const { state, data, error, reload } = skills;

  const nodes = useMemo(() => data?.nodes ?? [], [data]);
  const domains = useMemo(() => groupByDomain(nodes), [nodes]);
  const attempted = nodes.filter((node) => node.attempts > 0);
  const overall = attempted.length === 0 ? 0 : attempted.reduce((s, n) => s + n.mastery, 0) / attempted.length;
  const strongest = [...attempted].sort((a, b) => b.mastery - a.mastery)[0];
  const focus = useMemo(() => pickFocus(nodes), [nodes]);
  const focusChallenge = focus === null ? null : challengeFor(focus, catalogue.data?.challenges ?? []);

  if (state === 'loading') {
    return (
      <div className="space-y-4" aria-label="Loading skill map">
        <div className="grid gap-4 sm:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="skeleton h-28" />
          ))}
        </div>
        <div className="skeleton h-64" />
      </div>
    );
  }

  if (state === 'error') return <FetchFailure error={error} onRetry={reload} />;

  if (nodes.length === 0) {
    return (
      <p className="rounded-panel border border-edge bg-surface-raised p-10 text-center text-sm text-content-muted">
        No skill nodes exist yet. Seed the concept graph with <code className="font-mono">make seed</code>.
      </p>
    );
  }

  return (
    <div className="space-y-6">
      <div className="grid gap-4 md:grid-cols-4">
        <Stat label="Overall mastery" className="md:col-span-1">
          <div className="flex items-center gap-4">
            <ProgressRing value={overall} size={64} stroke={6} label="Overall mastery">
              <span className="font-mono text-xs font-semibold tabular-nums">
                {attempted.length === 0 ? '—' : formatMastery(overall)}
              </span>
            </ProgressRing>
            <p className="text-xs text-content-subtle">across {attempted.length} attempted skills</p>
          </div>
        </Stat>

        <Stat label="Coverage" icon={<Layers aria-hidden className="size-3.5" />}>
          <p className="text-3xl font-semibold tabular-nums">
            {attempted.length}
            <span className="text-lg text-content-subtle">/{nodes.length}</span>
          </p>
          <p className="mt-1 text-xs text-content-subtle">skills with evidence</p>
        </Stat>

        <Stat label="Strongest" icon={<Flame aria-hidden className="size-3.5" />}>
          {strongest === undefined ? (
            <p className="text-sm text-content-subtle">Nothing attempted yet.</p>
          ) : (
            <>
              <p className="truncate text-lg font-semibold">{sanitizeSingleLine(strongest.label, 40)}</p>
              <p className={cn('mt-1 font-mono text-xs', masteryToneClass(strongest.mastery, strongest.attempts))}>
                {formatMastery(strongest.mastery)}
              </p>
            </>
          )}
        </Stat>

        <div className="relative overflow-hidden rounded-panel border border-accent/40 bg-surface-raised p-5">
          <div aria-hidden className="absolute inset-0 bg-gradient-to-br from-accent/15 to-transparent" />
          <div className="relative">
            <p className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-accent">
              <Compass aria-hidden className="size-3.5" /> Up next
            </p>
            {focus === null ? (
              <p className="mt-2 text-sm text-content-muted">Every skill is strong. Impressive.</p>
            ) : (
              <>
                <p className="mt-2 truncate text-lg font-semibold">{sanitizeSingleLine(focus.label, 40)}</p>
                <p className="text-xs text-content-subtle">
                  {focus.attempts === 0 ? 'Not attempted yet' : `${formatMastery(focus.mastery)} mastery`}
                </p>
                {focusChallenge !== null ? (
                  <Link
                    href={`/challenges/${encodeURIComponent(focusChallenge.slug)}`}
                    className="mt-3 inline-flex items-center gap-1.5 text-sm font-medium text-accent hover:underline"
                  >
                    <Sparkles aria-hidden className="size-3.5" />
                    {sanitizeSingleLine(focusChallenge.title, 32)}
                    <ArrowRight aria-hidden className="size-3.5" />
                  </Link>
                ) : null}
              </>
            )}
          </div>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {domains.map((group, index) => (
          <section
            key={group.domain}
            aria-labelledby={`domain-${group.domain}`}
            className="animate-fade-up overflow-hidden rounded-panel border border-edge bg-surface-raised"
            style={{ animationDelay: `${index * 70}ms` }}
          >
            <div className="flex items-center gap-4 border-b border-edge px-5 py-4">
              <ProgressRing
                value={group.average ?? 0}
                size={44}
                stroke={4}
                label={`${domainLabel(group.domain)} mastery`}
                arcClassName={group.average === null ? 'stroke-edge' : 'stroke-accent'}
              >
                <span className="font-mono text-[10px] tabular-nums text-content-muted">
                  {group.average === null ? '—' : Math.round(group.average * 100)}
                </span>
              </ProgressRing>
              <div className="min-w-0 flex-1">
                <h2 id={`domain-${group.domain}`} className="text-base font-semibold">
                  {domainLabel(group.domain)}
                </h2>
                <p className="text-xs text-content-subtle">
                  {group.seen}/{group.nodes.length} attempted
                </p>
              </div>
            </div>
            <ul className="divide-y divide-edge/60">
              {group.nodes.map((node) => (
                <li key={node.id}>
                  <SkillRow node={node} />
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}

function Stat({
  label,
  icon,
  children,
  className,
}: {
  readonly label: string;
  readonly icon?: ReactNode;
  readonly children: ReactNode;
  readonly className?: string;
}): JSX.Element {
  return (
    <div className={cn('rounded-panel border border-edge bg-surface-raised p-5', className)}>
      <p className="mb-3 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-content-subtle">
        {icon}
        {label}
      </p>
      {children}
    </div>
  );
}

const BAND_LABELS = {
  unseen: 'Not attempted',
  weak: 'Weak',
  developing: 'Developing',
  strong: 'Strong',
} as const;

function SkillRow({ node }: { readonly node: SkillNode }): JSX.Element {
  const band = masteryBand(node.mastery, node.attempts);
  const percent = Math.round(Math.min(1, Math.max(0, node.mastery)) * 100);

  return (
    <div
      className={cn('flex items-center gap-4 px-5 py-2.5', band === 'unseen' && 'opacity-60')}
      style={{ paddingLeft: `${20 + Math.min(node.depth, 5) * 16}px` }}
    >
      <div className="min-w-0 flex-1">
        <p className={cn('truncate text-sm', node.depth === 0 ? 'font-semibold' : 'font-medium')}>
          {sanitizeSingleLine(node.label, 60)}
        </p>
        <p className="truncate font-mono text-[11px] text-content-subtle">
          {band === 'unseen'
            ? 'not attempted'
            : `${node.attempts} ${node.attempts === 1 ? 'try' : 'tries'} · ${formatRelativeTime(node.last_seen_at)}`}
        </p>
      </div>

      {/* role="meter": mastery is a scalar in a known range, not progress to a goal.
          aria-valuetext states the band so colour is not the only signal. */}
      <div
        role="meter"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuetext={`${BAND_LABELS[band]}, ${formatMastery(node.mastery)} mastery`}
        aria-label={sanitizeSingleLine(node.label, 60)}
        className={cn(
          'hidden h-2 w-32 shrink-0 overflow-hidden rounded-full sm:block',
          band === 'unseen' ? 'border border-dashed border-edge-strong' : 'bg-edge',
        )}
      >
        {band === 'unseen' ? null : (
          <div
            className={cn('h-full rounded-full transition-[width] duration-700', masteryBarClass(node.mastery, node.attempts))}
            style={{ width: `${percent}%` }}
          />
        )}
      </div>

      <span
        className={cn(
          'w-12 shrink-0 text-right font-mono text-xs tabular-nums',
          masteryToneClass(node.mastery, node.attempts),
        )}
      >
        {band === 'unseen' ? '—' : formatMastery(node.mastery)}
      </span>
    </div>
  );
}

function FetchFailure({ error, onRetry }: { readonly error: unknown; readonly onRetry: () => void }): JSX.Element {
  const offline = isApiError(error) && error.isOffline;
  const unauthorized = isApiError(error) && error.isUnauthorized;

  return (
    <div className="rounded-panel border border-edge bg-surface-raised p-8">
      {unauthorized ? (
        <TriangleAlert aria-hidden className="size-7 text-accent" />
      ) : (
        <ServerCrash aria-hidden className="size-7 text-verdict-infra" />
      )}
      <h2 className="mt-3 font-semibold">
        {unauthorized
          ? 'Sign in to see your skill map'
          : offline
            ? 'Cannot reach the skill service'
            : 'Could not load your skill map'}
      </h2>
      <p className="mt-1 max-w-md text-sm text-content-muted">
        {unauthorized
          ? 'Mastery is per candidate, so it is only available to a signed-in account.'
          : errorMessage(error, 'The request failed unexpectedly.')}
      </p>
      <div className="mt-4">
        {unauthorized ? (
          <Link
            href="/login?next=/dashboard"
            className="inline-flex h-9 items-center rounded-lg bg-accent px-4 text-sm font-medium text-accent-fg shadow-glow-sm"
          >
            Go to sign in
          </Link>
        ) : (
          <Button size="sm" onClick={onRetry}>
            Try again
          </Button>
        )}
      </div>
    </div>
  );
}
