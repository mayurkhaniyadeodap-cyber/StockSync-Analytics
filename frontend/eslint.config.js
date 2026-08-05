import js from '@eslint/js';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist', 'node_modules', 'coverage'] },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      // Tokens must never be logged, and console noise hides real problems.
      'no-console': ['error', { allow: ['warn', 'error'] }],
      eqeqeq: ['error', 'always', { null: 'ignore' }],
    },
  },
  {
    // Each context file exports both the context object and its provider.
    // Splitting them would buy nothing but marginally better hot-reload; the
    // consuming hooks already live in src/hooks so components import from there.
    files: ['src/contexts/*.tsx'],
    rules: { 'react-refresh/only-export-components': 'off' },
  },
  {
    files: ['**/*.test.ts', '**/*.test.tsx', 'tests/**/*.ts'],
    languageOptions: { globals: globals.node },
  },
  {
    // Invented figures live in tests/fixtures and must stay there. Without this
    // a page could import a fixture and ship data no backend ever sent — the
    // one failure mode that looks completely correct on screen.
    files: ['src/**/*.{ts,tsx}'],
    ignores: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          paths: [
            {
              name: 'axios',
              message:
                'Use the client in src/lib/api.ts. A second HTTP layer would not send the ' +
                'session cookie or renew an expired one.',
            },
          ],
          patterns: [
            {
              group: ['**/tests/**', '**/fixtures/**'],
              message: 'Test fixtures are not real data. Production code must read the API.',
            },
          ],
        },
      ],
    },
  },
  {
    /*
     * One way to reach the API, and it is `src/lib/api.ts`.
     *
     * A bare `fetch` looks harmless and is not: it omits `credentials`, so the
     * httpOnly session cookie is never sent and the call 401s on its first try;
     * and it never reaches the renew-and-replay path, so it would fail outright
     * fifteen minutes into a session where every other request recovers. That
     * combination is invisible in review and intermittent in use, which is
     * exactly the bug this codebase already spent a day finding.
     *
     * Tests are exempt — they stub the global to script responses.
     */
    files: ['src/**/*.{ts,tsx}'],
    ignores: ['src/lib/api.ts', 'src/**/*.test.ts', 'src/**/*.test.tsx'],
    rules: {
      'no-restricted-globals': [
        'error',
        {
          name: 'fetch',
          message:
            'Use `api` from src/lib/api.ts. A bare fetch sends no session cookie and gets ' +
            'no automatic renewal, so it starts failing fifteen minutes into a session.',
        },
        {
          name: 'XMLHttpRequest',
          message:
            'Use `api` from src/lib/api.ts, which sends the session cookie and renews it.',
        },
      ],
      'no-restricted-properties': [
        'error',
        {
          object: 'window',
          property: 'fetch',
          message: 'Use `api` from src/lib/api.ts — window.fetch bypasses the session layer.',
        },
        {
          object: 'globalThis',
          property: 'fetch',
          message:
            'Use `api` from src/lib/api.ts — globalThis.fetch bypasses the session layer.',
        },
      ],
    },
  },
  {
    files: ['vite.config.ts', 'eslint.config.js'],
    languageOptions: { globals: globals.node },
  },
);
