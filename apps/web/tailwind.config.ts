/**
 * Tailwind configuration.
 *
 * The dark theme is a first-class token set rather than an inverted light theme.
 * The IDE view is the primary surface and candidates spend long stretches in it,
 * so the dark palette is designed directly: a near-black editor background,
 * elevated panel surfaces, and syntax colours checked for contrast against
 * `--surface` rather than against white.
 *
 * `darkMode: "class"` rather than `media`, so the candidate's explicit choice
 * wins over their OS setting.
 */
import type { Config } from 'tailwindcss';

const config: Config = {
  darkMode: 'class',
  content: ['./src/**/*.{ts,tsx,mdx}'],
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: 'rgb(var(--surface) / <alpha-value>)',
          raised: 'rgb(var(--surface-raised) / <alpha-value>)',
          sunken: 'rgb(var(--surface-sunken) / <alpha-value>)',
        },
        content: {
          DEFAULT: 'rgb(var(--content) / <alpha-value>)',
          muted: 'rgb(var(--content-muted) / <alpha-value>)',
          subtle: 'rgb(var(--content-subtle) / <alpha-value>)',
        },
        edge: 'rgb(var(--edge) / <alpha-value>)',
        verdict: {
          pass: 'rgb(var(--verdict-pass) / <alpha-value>)',
          fail: 'rgb(var(--verdict-fail) / <alpha-value>)',
          skip: 'rgb(var(--verdict-skip) / <alpha-value>)',
          infra: 'rgb(var(--verdict-infra) / <alpha-value>)',
        },
      },
      fontFamily: {
        sans: ['var(--font-sans)'],
        mono: ['var(--font-mono)'],
      },
      borderRadius: { panel: '0.5rem' },
      keyframes: {
        'slide-in': {
          from: { opacity: '0', transform: 'translateY(4px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'stream-cursor': { '0%, 49%': { opacity: '1' }, '50%, 100%': { opacity: '0' } },
      },
      animation: {
        'slide-in': 'slide-in 120ms ease-out',
        'stream-cursor': 'stream-cursor 1s step-end infinite',
      },
    },
  },
  plugins: [],
};

export default config;
