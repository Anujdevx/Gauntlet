'use client';

/**
 * Sign in and sign up on one route.
 *
 * Two details are load-bearing rather than cosmetic:
 *
 *  - The error region is `role="alert"`, so it is announced the moment it
 *    appears. A visually-styled message that a screen reader never mentions is
 *    the usual reason a failed sign-in looks like the button simply did nothing.
 *  - The post-login redirect target comes from `?next=` and is validated to be a
 *    single-slash-prefixed same-origin path. Trusting the parameter unchecked is
 *    an open redirect: `?next=https://evil.example` would land a candidate on a
 *    convincing copy of this sign-in page right after they authenticate.
 */
import { Suspense, useId, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import type { FormEvent as ReactFormEvent } from 'react';

import { AppShell } from '@/components/layout/AppShell';
import { Button } from '@/components/ui/Button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card';
import { Spinner } from '@/components/ui/Spinner';
import { useSessionStore } from '@/store/session';

type Mode = 'signin' | 'signup';

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function LoginPage(): JSX.Element {
  return (
    <AppShell>
      <Suspense fallback={<LoginSkeleton />}>
        <LoginForm />
      </Suspense>
    </AppShell>
  );
}

/**
 * `useSearchParams` opts a route into client-side rendering, which Next requires
 * a Suspense boundary for during prerendering. Without this wrapper the build
 * fails on `useSearchParams() should be wrapped in a suspense boundary`.
 */
function LoginForm(): JSX.Element {
  const router = useRouter();
  const searchParams = useSearchParams();
  const formId = useId();

  const signIn = useSessionStore((state) => state.signIn);
  const signUp = useSessionStore((state) => state.signUp);
  const loading = useSessionStore((state) => state.loading);
  const error = useSessionStore((state) => state.error);

  const [mode, setMode] = useState<Mode>('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [fieldErrors, setFieldErrors] = useState<{ email?: string; password?: string; displayName?: string }>({});

  const destination = safeNextPath(searchParams.get('next'));
  const isSignUp = mode === 'signup';

  const onSubmit = async (event: ReactFormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    const problems = validate({ email, password, displayName, mode });
    setFieldErrors(problems);
    if (Object.keys(problems).length > 0) {
      return;
    }
    const ok = isSignUp
      ? await signUp(email, password, displayName.trim())
      : await signIn(email, password);
    if (ok) {
      router.replace(destination);
    }
  };

  return (
    <div className="mx-auto flex w-full max-w-md flex-col justify-center px-4 py-12">
      <Card>
        <CardHeader>
          <CardTitle>{isSignUp ? 'Create an account' : 'Sign in'}</CardTitle>
          <CardDescription>
            {isSignUp
              ? 'An account lets the skill matrix track what you have demonstrated.'
              : 'Sign in to continue an assessment.'}
          </CardDescription>
        </CardHeader>

        <CardContent>
          <form noValidate onSubmit={(event) => void onSubmit(event)} className="space-y-4">
            {isSignUp ? (
              <Field
                id={`${formId}-name`}
                label="Display name"
                type="text"
                autoComplete="name"
                value={displayName}
                error={fieldErrors.displayName}
                onChange={setDisplayName}
              />
            ) : null}

            <Field
              id={`${formId}-email`}
              label="Email"
              type="email"
              autoComplete="email"
              value={email}
              error={fieldErrors.email}
              onChange={setEmail}
            />

            <Field
              id={`${formId}-password`}
              label="Password"
              type="password"
              autoComplete={isSignUp ? 'new-password' : 'current-password'}
              value={password}
              error={fieldErrors.password}
              onChange={setPassword}
              hint={isSignUp ? 'At least 8 characters.' : undefined}
            />

            {error !== null ? (
              <p
                role="alert"
                className="rounded border border-verdict-fail/40 bg-verdict-fail/10 px-3 py-2 text-sm text-verdict-fail"
              >
                {error}
              </p>
            ) : null}

            <Button type="submit" variant="primary" block disabled={loading} className="w-full">
              {loading ? <Spinner /> : null}
              {isSignUp ? 'Create account' : 'Sign in'}
            </Button>
          </form>

          <div className="mt-4 border-t border-edge pt-4 text-center text-sm">
            <button
              type="button"
              onClick={() => {
                setMode(isSignUp ? 'signin' : 'signup');
                setFieldErrors({});
              }}
              className="rounded text-content-muted underline underline-offset-4 hover:text-content"
            >
              {isSignUp ? 'Already have an account? Sign in' : 'Need an account? Sign up'}
            </button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

interface FieldErrors {
  readonly email?: string;
  readonly password?: string;
  readonly displayName?: string;
}

function validate(input: {
  readonly email: string;
  readonly password: string;
  readonly displayName: string;
  readonly mode: Mode;
}): FieldErrors {
  const errors: { email?: string; password?: string; displayName?: string } = {};
  if (input.email.trim() === '' || !EMAIL_PATTERN.test(input.email.trim())) {
    errors.email = 'Enter a valid email address.';
  }
  if (input.mode === 'signup' && input.password.length < 8) {
    errors.password = 'Use at least 8 characters.';
  }
  if (input.mode === 'signup' && input.displayName.trim() === '') {
    errors.displayName = 'Enter a display name.';
  }
  return errors;
}

/**
 * Constrain `?next=` to an in-app path.
 *
 * Rejects absolute URLs and protocol-relative forms. Requiring a leading single
 * slash and rejecting `//` or `/\` covers both: an attacker-supplied
 * `https://evil.example` and a `//evil.example` both fail.
 */
function safeNextPath(candidate: string | null): string {
  if (candidate === null) {
    return '/dashboard';
  }
  const decoded = (() => {
    try {
      return decodeURIComponent(candidate);
    } catch {
      return candidate;
    }
  })();
  if (!decoded.startsWith('/') || decoded.startsWith('//') || decoded.startsWith('/\\')) {
    return '/dashboard';
  }
  return decoded;
}

interface FieldProps {
  readonly id: string;
  readonly label: string;
  readonly type: 'text' | 'email' | 'password';
  readonly autoComplete: string;
  readonly value: string;
  readonly error?: string;
  readonly hint?: string;
  readonly onChange: (value: string) => void;
}

/** Labelled input with its error wired through `aria-describedby`. */
function Field({
  id,
  label,
  type,
  autoComplete,
  value,
  error,
  hint,
  onChange,
}: FieldProps): JSX.Element {
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [hint === undefined ? null : hintId, error === undefined ? null : errorId]
    .filter((described): described is string => described !== null)
    .join(' ');

  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-sm font-medium">
        {label}
      </label>
      <input
        id={id}
        name={id}
        type={type}
        autoComplete={autoComplete}
        value={value}
        required
        aria-invalid={error !== undefined}
        {...(describedBy === '' ? {} : { 'aria-describedby': describedBy })}
        onChange={(event) => onChange(event.target.value)}
        className="block w-full rounded-md border border-edge bg-surface px-3 py-2 text-sm text-content placeholder:text-content-subtle"
      />
      {hint !== undefined ? (
        <p id={hintId} className="text-xs text-content-subtle">
          {hint}
        </p>
      ) : null}
      {error !== undefined ? (
        <p id={errorId} className="text-xs text-verdict-fail">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function LoginSkeleton(): JSX.Element {
  return (
    <div className="mx-auto flex w-full max-w-md flex-col justify-center px-4 py-12">
      <div className="flex items-center justify-center rounded-panel border border-edge py-16">
        <Spinner label="Loading sign-in form" />
      </div>
    </div>
  );
}
