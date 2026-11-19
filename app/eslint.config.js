// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Flat config (ESLint 9+). Non-type-checked on purpose: tsc --noEmit already
// gates types twice per pipeline (make test-app and the vite build), so
// type-aware linting would duplicate an existing gate at 3-4x the lint time.
// No stylistic layer — the codebase has a consistent hand style.
import eslint from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default [
  { ignores: ["dist"] },
  eslint.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}", "vite.config.ts"],
    languageOptions: { globals: globals.browser },
    plugins: { "react-hooks": reactHooks },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      // ECharts option plumbing is deliberately untyped — not a fight worth having
      "@typescript-eslint/no-explicit-any": "off",
      // `while (true)` is the intentional-loop idiom here
      "no-constant-condition": ["error", { checkLoops: false }],
      // tsc's noUnusedLocals is the enforcement point; align the eslint twin
      "@typescript-eslint/no-unused-vars": [
        "error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
  // the theme script index.html loads as a plain browser script, before the bundle
  { files: ["build/theme.js"], languageOptions: { globals: globals.browser, sourceType: "script" } },
];
