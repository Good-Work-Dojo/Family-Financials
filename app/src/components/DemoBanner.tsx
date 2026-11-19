// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { DEMO_BANNER_TEXT, IS_DEMO, storageKey } from "../lib/demo";
import { localFlagStore } from "../lib/localFlagStore";

/** this browser closed the demo banner (a storage that refuses the write keeps it closed for the page view) */
export const demoBannerClosed = localFlagStore<boolean>(storageKey("demo_banner_closed"),(raw) => raw === "1",
  (closed) => (closed ? "1" : null));

/** The demo build's banner (lib/demo.ts): what the site is, above every page, in the page's flow so it covers nothing,
 *  until this browser closes it. Nothing at all in any other build. */
export default function DemoBanner() {
  const closed = demoBannerClosed.use();
  if (!IS_DEMO || closed) return null;
  return (
    <div role="note" aria-label="About this demo" data-testid="demo-banner"
      className="flex items-center gap-2 border-b border-zinc-800 bg-blue-950 px-3 py-2 text-sm text-blue-200 print:hidden sm:px-5">
      <p className="min-w-0 grow">{DEMO_BANNER_TEXT}</p>
      <button type="button" onClick={() => demoBannerClosed.set(true)} aria-label="Close the demo notice"
        className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg hover:bg-zinc-800 md:h-8 md:w-8">
        <span aria-hidden="true">✕</span>
      </button>
    </div>
  );
}
