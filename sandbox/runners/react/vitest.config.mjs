/**
 * Vitest configuration for React grading.
 *
 * Every setting here exists to remove a source of non-determinism from grading:
 *
 * - `environment: jsdom` rather than a real browser: no WebGL, no font metrics,
 *   no compositor timing.
 * - `globals: false` and explicit imports: no reliance on ambient state that a
 *   candidate's setup file could have mutated.
 * - `pool: 'forks'` with a single fork: worker-thread scheduling differences
 *   between runs would otherwise make the same submission pass on a loaded
 *   machine and fail on an idle one.
 * - `sequence.shuffle: false` and a fixed `seed`: test order must not affect
 *   outcomes, since the candidate controls which files exist.
 * - `testTimeout` matching the per-test cap enforced by the Python image, so all
 *   three runtimes behave alike.
 *
 * The reporter writes to `OA_RESULT_PATH`, which the worker reads. The exit code
 * is recorded but never used as a verdict.
 */
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

const resultPath = process.env.OA_RESULT_PATH || '/grading/result.json';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: false,
    setupFiles: [],
    include: ['/grading/**/*.test.{js,jsx,ts,tsx}'],
    passWithNoTests: false,
    pool: 'forks',
    poolOptions: { forks: { singleFork: true } },
    sequence: { shuffle: false, concurrent: false },
    testTimeout: 10_000,
    hookTimeout: 10_000,
    teardownTimeout: 5_000,
    reporters: ['default', 'json'],
    outputFile: { json: resultPath },
    // A candidate's own vite/vitest config must not be able to redirect the
    // reporter, change the environment, or disable the sandbox's assertions.
    // `configFile: false` means this file is the only configuration in effect.
    configFile: false,
    root: '/workspace',
    isolate: true,
    clearMocks: true,
    restoreMocks: true,
    unstubEnvs: true,
    unstubGlobals: true,
    // esbuild cannot reach the network, and offline is explicit so a stray
    // import fails fast instead of hanging until the wall clock kills it.
    deps: { optimizer: { web: { enabled: false } } },
  },
  esbuild: { legalComments: 'none' },
});
