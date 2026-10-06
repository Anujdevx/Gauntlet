'use client';

/**
 * Skill matrix: mastery across the concept graph, grouped by domain.
 *
 * The design problem is that a Bayesian mastery posterior of 0 is ambiguous.
 * Zero attempts and zero skill look identical in the data, and rendering both as
 * a red 0% bar tells a candidate they are weak at something they have never been
 * asked. Unseen nodes are therefore rendered distinctly, and every bar is paired
 * with its numeric value so the colour is never the only signal.
 *
 * Every failure mode renders a panel. An unreachable API is the normal condition
 * during local development and must not take the route down with it.
 */
import { useMemo } from 'react';
import { ServerCrash, TriangleAlert } from 'lucide-react';

import { AppShell } from '@/components/layout/AppShell';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { Spinner } from '@/components/ui/Spinner';
import { cn } from '@/lib/cn';
import { errorMessage, fetchSkills, isApiError } from '@/lib/api';
import {
  formatMastery,
  formatRelativeTime,
  masteryBarClass,
  masteryBand,
  sanitizeSingleLine,
} from '@/lib/format';
import { useResource } from '@/hooks/useResource';
import type { SkillNode } from '@/types/api';

export default function DashboardPage(): JSX.Element {
  return (
    <AppShell>
      <div className="mx-auto w-full max-w-5xl px-4 py-8">
        <header className="mb-6">
          <h1 className="text-xl font-semibold tracking-tight">Skill matrix</h1>
          <p className="mt-1 text-sm text-content-muted">
            What the model believes you have demonstrated, and where the evidence is thin.
          </p>
        </header>
        <SkillMatrix />
      </div>
    </AppShell>
  );
}

function SkillMatrix(): JSX.Element {
  const resource = useResource((signal) => fetchSkills(signal), []);
  const { state, data, error, reload } = resource;

  const domains = useMemo(() => groupByDomain(data?.nodes ?? []), [data]);

  if (state === 'loading') {
    return (
      <div className="flex justify-center py-16">
        <Spinner label="Loading skill matrix" />
      </div>
    );
  }

  if (state === 'error') {
    return <FetchFailure error={error} onRetry={reload} />;
  }

  if (data === null || data.nodes.length === 0) {
    return (
      <Card>
        <CardContent className="py-10 text-center">
          <p className="text-sm text-content-muted">
            No skill nodes have been observed yet. Attempt a challenge and the graph fills in
            from the results.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      {domains.map((group) => (
        <section key={group.domain} aria-labelledby={`domain-${group.domain}`}>
          <Card>
            <CardHeader className="flex-row items-center justify-between gap-3">
              <CardTitle id={`domain-${group.domain}`} className="capitalize">
                {group.domain}
              </CardTitle>
              <span className="text-xs text-content-subtle">
                {group.seen}/{group.nodes.length} attempted
              </span>
            </CardHeader>
            <CardContent className="p-0">
              <ul className="divide-y divide-edge">
                {group.nodes.map((node) => (
                  <li key={node.id}>
                    <SkillRow node={node} />
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        </section>
      ))}
    </div>
  );
}

interface DomainGroup {
  readonly domain: string;
  readonly nodes: readonly SkillNode[];
  readonly seen: number;
}

/**
 * Group nodes by domain, ordered shallowest first.
 *
 * `depth` rather than `parent_id` drives ordering and indentation: it survives a
 * partially-materialised graph, where some parents may not be present in the
 * response, and it needs no second pass to build.
 */
function groupByDomain(nodes: readonly SkillNode[]): readonly DomainGroup[] {
  const byDomain = new Map<string, SkillNode[]>();
  for (const node of nodes) {
    const bucket = byDomain.get(node.domain);
    if (bucket === undefined) {
      byDomain.set(node.domain, [node]);
    } else {
      bucket.push(node);
    }
  }

  return [...byDomain.entries()]
    .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
    .map(([domain, entries]) => {
      const ordered = [...entries].sort((left, right) => {
        if (left.depth !== right.depth) {
          return left.depth - right.depth;
        }
        return left.id < right.id ? -1 : left.id > right.id ? 1 : 0;
      });
      return {
        domain,
        nodes: ordered,
        seen: ordered.filter((node) => node.attempts > 0).length,
      };
    });
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
      className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5"
      // Indent by depth so the hierarchy is legible without drawing edges.
      style={{ paddingLeft: `${16 + Math.min(node.depth, 6) * 14}px` }}
    >
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{sanitizeSingleLine(node.label, 80)}</p>
        {/* The id is the stable identifier a candidate can quote in a bug report. */}
        <p className="truncate font-mono text-xs text-content-subtle">{sanitizeSingleLine(node.id, 120)}</p>
      </div>

      <div className="w-40 shrink-0">
        {/*
          `role="meter"` rather than `progressbar`: mastery is a scalar within a
          known range, not progress toward a goal. `aria-valuetext` states the
          band so the meaning is not carried by colour alone.
        */}
        <div
          role="meter"
          aria-valuenow={percent}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuetext={`${BAND_LABELS[band]}, ${formatMastery(node.mastery)} mastery`}
          aria-label={sanitizeSingleLine(node.label, 80)}
          className={cn(
            'h-1.5 w-full overflow-hidden rounded-full',
            band === 'unseen' ? 'bg-surface-sunken' : 'bg-edge',
          )}
        >
          {band === 'unseen' ? null : (
            <div className={cn('h-full rounded-full', masteryBarClass(node.mastery, node.attempts))} style={{ width: `${percent}%` }} />
          )}
        </div>
      </div>

      <span className="w-20 shrink-0 text-right font-mono text-xs tabular-nums text-content-muted">
        {band === 'unseen' ? '—' : formatMastery(node.mastery)}
      </span>

      <span className="w-16 shrink-0 text-right text-xs text-content-subtle tabular-nums">
        {node.attempts} {node.attempts === 1 ? 'try' : 'tries'}
      </span>

      <Badge tone={band === 'unseen' ? 'neutral' : 'accent'} className="shrink-0">
        {BAND_LABELS[band]}
      </Badge>

      <span className="w-24 shrink-0 text-right text-xs text-content-subtle">
        {formatRelativeTime(node.last_seen)}
      </span>
    </div>
  );
}

/**
 * Distinct handling for an unreachable API and for an API that answered with an
 * error. The first is ours to fix, the second is usually the candidate's session.
 */
function FetchFailure({ error, onRetry }: { readonly error: unknown; readonly onRetry: () => void }): JSX.Element {
  const offline = isApiError(error) && error.isOffline;
  const unauthorized = isApiError(error) && error.isUnauthorized;

  return (
    <Card>
      <CardContent className="flex flex-col items-start gap-3 py-8">
        {unauthorized ? (
          <TriangleAlert aria-hidden className="size-6 text-verdict-infra" />
        ) : (
          <ServerCrash aria-hidden className="size-6 text-verdict-infra" />
        )}
        <div>
          <h2 className="text-sm font-semibold">
            {unauthorized
              ? 'Sign in to see your skill matrix'
              : offline
                ? 'Cannot reach the skill service'
                : 'Could not load your skill matrix'}
          </h2>
          <p className="mt-1 text-sm text-content-muted">
            {unauthorized
              ? 'Mastery is per candidate, so it is only available to a signed-in account.'
              : errorMessage(error, 'The request failed unexpectedly.')}
          </p>
        </div>
        {unauthorized ? (
          <Button variant="primary" size="sm" onClick={() => (window.location.assign('/login'))}>
            Go to sign in
          </Button>
        ) : (
          <Button size="sm" onClick={onRetry}>
            Try again
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
