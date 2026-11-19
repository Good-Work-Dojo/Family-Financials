// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// clock.freeze: the demo build stands its clock at the exports' generation time (data/DataContext.tsx); this is the
// primitive, in any build.
import { afterEach, describe, expect, it } from "vitest";
import { clock } from "./clock";

afterEach(() => clock.freeze(null));

describe("clock.freeze stands the clock at one instant", () => {
  it("every read is that instant, the household's day and the UTC stamp with it, until the wall clock is given back", () => {
    clock.freeze("2026-10-31T20:15:00-04:00");
    expect(clock.now().toISOString()).toBe("2026-11-01T00:15:00.000Z");
    expect(clock.nowISO()).toBe("2026-11-01T00:15:00.000Z");
    expect(clock.today("America/New_York")).toBe("2026-10-31");
    expect(clock.today("UTC")).toBe("2026-11-01");
    const read = clock.now();
    read.setFullYear(2000);
    expect(clock.now().getUTCFullYear(), "each read is a fresh Date: changing one moves nothing").toBe(2026);
    clock.freeze(null);
    expect(Math.abs(clock.now().getTime() - Date.now())).toBeLessThan(1000);
  });

  it("an instant that does not parse leaves the clock as it was", () => {
    clock.freeze("2026-06-10T18:00:00Z");
    clock.freeze("not a time");
    expect(clock.nowISO()).toBe("2026-06-10T18:00:00.000Z");
  });
});
