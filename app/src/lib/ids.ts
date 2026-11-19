// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
/** v4 UUID built on getRandomValues, not crypto.randomUUID: randomUUID exists
 *  only in secure contexts (HTTPS/localhost) and the deployed dashboard is
 *  plain-HTTP on the LAN — and a single implementation means dev and the
 *  deploy run the same code path. */
export function uuid4(): string {
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

/** A stand-in for an id that has not been minted yet, the length of a real one (a v4 UUID, 36 characters): the
 *  UUID's own template, so it is no id anything could hold. A save PREVIEW (the box that says "full" or "too long"
 *  before the click) builds the item with this, so it measures the same bytes the real save will: a shorter
 *  placeholder passes entries the real id refuses. */
export const PREVIEW_ID = "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx";

/** A stand-in for a `created_at` stamp, exactly as long as a real one (`clock.nowISO()`, 24 characters): the
 *  stamp's own template, so it is no date. The save preview builds its item with this rather than reading the clock on
 *  every keystroke: the byte count is what the preview measures, and a constant keeps the render pure. */
export const PREVIEW_STAMP = "YYYY-MM-DDTHH:mm:ss.sssZ";
