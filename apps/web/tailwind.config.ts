/**
 * Tailwind configuration for Gauntlet.
 *
 * Both themes are first-class token sets rather than one inverted from the other.
 * The IDE is the primary surface and candidates spend long stretches in it, so
 * the dark palette is designed directly: a warm near-black, one ember accent, and
 * verdict colours checked for contrast against `--surface`.
 *
 * `darkMode: "class"` rather than `media`, so the candidate's explicit choice
 * wins over their OS setting.
 */
import type { Config } from 'tailwindcss';

const channel = (name: string): string => `rgb(var(--${name}) / <alpha-value>)`;

const config: Config = {
  darkMode: 'class',
  content: ['./src/**/*.{ts,tsx,mdx}'],
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: channel('surface'),
          raised: channel('surface-raised'),
          sunken: channel('surface-sunken'),
          overlay: channel('surface-overlay'),
        },
        content: {
          DEFAULT: channel('content'),
          muted: channel('content-muted'),
          subtle: channel('content-subtle'),
        },
        edge: { DEFAULT: channel('edge'), strong: channel('edge-strong') },
        accent: { DEFAULT: channel('accent'), fg: channel('accent-fg') },
        verdict: {
          pass: channel('verdict-pass'),
          fail: channel('verdict-fail'),
          skip: channel('verdict-skip'),
          infra: channel('verdict-infra'),
        },
      },
      fontFamily: {
        sans: ['var(--font-sans)'],
        mono: ['var(--font-mono)'],
      },
      borderRadius: { panel: '0.75rem' },
      boxShadow: {
        glow: '0 0 0 1px rgb(var(--accent) / 0.5), 0 4px 24px -4px rgb(var(--accent) / 0.45)',
        'glow-sm': '0 0 16px -2px rgb(var(--accent) / 0.55)',
        pop: '0 12px 40px -12px rgb(0 0 0 / 0.55)',
      },
      keyframes: {
        'fade-up': {
          from: { opacity: '0', transform: 'translateY(10px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'slide-in': {
          from: { opacity: '0', transform: 'translateX(-6px)' },
          to: { opacity: '1', transform: 'translateX(0)' },
        },
        'stream-cursor': { '0%, 49%': { opacity: '1' }, '50%, 100%': { opacity: '0' } },
        pop: {
          '0%': { transform: 'scale(0.6)', opacity: '0' },
          '60%': { transform: 'scale(1.08)', opacity: '1' },
          '100%': { transform: 'scale(1)' },
        },
        shimmer: { from: { backgroundPosition: '200% 0' }, to: { backgroundPosition: '-200% 0' } },
        'pulse-ring': {
          '0%': { boxShadow: '0 0 0 0 rgb(var(--accent) / 0.55)' },
          '100%': { boxShadow: '0 0 0 10px rgb(var(--accent) / 0)' },
        },
        float: { '0%, 100%': { transform: 'translateY(0)' }, '50%': { transform: 'translateY(-6px)' } },
        confetti: {
          '0%': { transform: 'translate(0, 0) rotate(0deg)', opacity: '1' },
          '100%': {
            transform: 'translate(var(--dx), var(--dy)) rotate(var(--rot))',
            opacity: '0',
          },
        },
      },
      animation: {
        'fade-up': 'fade-up 450ms cubic-bezier(0.22, 1, 0.36, 1) both',
        'slide-in': 'slide-in 220ms ease-out both',
        'stream-cursor': 'stream-cursor 1s step-end infinite',
        pop: 'pop 380ms cubic-bezier(0.34, 1.56, 0.64, 1) both',
        shimmer: 'shimmer 2.4s linear infinite',
        'pulse-ring': 'pulse-ring 1.4s ease-out infinite',
        float: 'float 5s ease-in-out infinite',
        confetti: 'confetti 1100ms cubic-bezier(0.15, 0.7, 0.3, 1) forwards',
      },
    },
  },
  plugins: [],
};

export default config;
