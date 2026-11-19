// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { Plugin } from "vite";

/** The demo site's content-security policy: the web server's (deploy/Caddyfile's Content-Security-Policy header) less
 *  `frame-ancestors`, which a policy given in a <meta> cannot carry. tools/verify/demo_site.py holds the built page to
 *  the Caddyfile's policy. */
export const DEMO_CSP = "default-src 'none'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; "
  + "img-src 'self' data:; font-src 'self'; connect-src 'self'; manifest-src 'self'; form-action 'self'; base-uri 'none'";

/** The demo build's page (vite.config.ts, VITE_DEMO=1): a static host sends no policy header, so the page carries the
 *  policy itself, as the first thing in its head, before anything it governs. */
export function demoPagePlugin(): Plugin {
  return {
    name: "ff-demo-page",
    apply: "build",
    transformIndexHtml: () => [
      { tag: "meta", attrs: { "http-equiv": "Content-Security-Policy", content: DEMO_CSP }, injectTo: "head-prepend" },
    ],
  };
}
