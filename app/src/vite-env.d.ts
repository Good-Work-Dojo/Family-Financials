// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "1" in the demo build (lib/demo.ts) */
  readonly VITE_DEMO?: string;
  /** where the program's source is published (lib/product.ts) */
  readonly VITE_SOURCE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
