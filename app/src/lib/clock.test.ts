// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// The clock in every process zone: the gate runs this file in UTC with the whole suite and again in America/New_York
// and in Pacific/Kiritimati (tools/verify/ci.sh's zone runs), and each run must give the household's day for the same
// instants, whatever zone the browser is in. Only the fallback (no zone, or one the browser refuses) follows the
// process's own zone.
import { afterEach, describe, expect, it, vi } from "vitest";
import { clock } from "./clock";

/** Freezes `Date` at an instant, as the tests' demo clock and the screenshot harness do. */
const at = (iso: string) => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(iso));
};
afterEach(() => { vi.useRealTimers(); });

// each instant, and the day it is in four zones: New York runs four hours behind UTC in July and five in January,
// Kiritimati fourteen ahead all year and Pago Pago eleven behind all year - twenty-five hours from Kiritimati, so one
// instant can fall on three different days (New York and Kiritimati, eighteen hours apart, never show more than two)
const DAYS: [string, Record<string, string>][] = [
  ["2026-07-20T03:30:00Z", { UTC: "2026-07-20", "America/New_York": "2026-07-19", "Pacific/Kiritimati": "2026-07-20",
    "Pacific/Pago_Pago": "2026-07-19" }],
  ["2026-07-20T10:30:00Z", { UTC: "2026-07-20", "America/New_York": "2026-07-20", "Pacific/Kiritimati": "2026-07-21",
    "Pacific/Pago_Pago": "2026-07-19" }],
  ["2026-07-20T11:00:00Z", { UTC: "2026-07-20", "America/New_York": "2026-07-20", "Pacific/Kiritimati": "2026-07-21",
    "Pacific/Pago_Pago": "2026-07-20" }],
  ["2026-01-15T04:30:00Z", { UTC: "2026-01-15", "America/New_York": "2026-01-14", "Pacific/Kiritimati": "2026-01-15",
    "Pacific/Pago_Pago": "2026-01-14" }],
  ["2026-12-31T23:30:00Z", { UTC: "2026-12-31", "America/New_York": "2026-12-31", "Pacific/Kiritimati": "2027-01-01",
    "Pacific/Pago_Pago": "2026-12-31" }],
];
const browserZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone;

describe("clock.today is the household's day", () => {
  it("at each instant, the day in the household's zone, whatever zone the browser runs in", () => {
    for (const [instant, days] of DAYS) {
      at(instant);
      for (const [zone, day] of Object.entries(days)) expect(clock.today(zone), `${instant} in ${zone}`).toBe(day);
    }
  });

  it("one instant shows three different days, and the clock gives each zone its own", () => {
    const [instant, days] = DAYS.find(([, d]) => new Set(Object.values(d)).size === 3)!;
    at(instant);
    const seen = new Set(["Pacific/Pago_Pago", "UTC", "Pacific/Kiritimati"].map((zone) => clock.today(zone)));
    expect([...seen].sort(), instant).toEqual([days["Pacific/Pago_Pago"], days.UTC, days["Pacific/Kiritimati"]]);
    expect(seen.size).toBe(3);
  });

  it("with no zone, or one the browser refuses, the browser's own day, and nothing throws", () => {
    for (const [instant, days] of DAYS) {
      at(instant);
      const own = clock.today(browserZone());
      // in the three zones the gate runs this file in, the browser's day is the table's
      if (browserZone() in days) expect(own, `${instant} in the browser's ${browserZone()}`).toBe(days[browserZone()]);
      for (const zone of [null, "", "Not/AZone", "America/Atlantis"]) {
        expect(clock.today(zone), `${instant} with ${JSON.stringify(zone)}`).toBe(own);
      }
    }
  });

  it("reads the clock at every call, so a frozen or moved Date reaches it", () => {
    at("2026-07-20T03:30:00Z");
    expect(clock.today("Pacific/Kiritimati")).toBe("2026-07-20");
    vi.setSystemTime(new Date("2026-07-20T11:00:00Z"));
    expect(clock.today("Pacific/Kiritimati")).toBe("2026-07-21");
  });
});

describe("clock.now and clock.nowISO are the real instant", () => {
  it("the frozen instant itself, and a UTC stamp with a Z whatever the zone (never the household's wall clock)", () => {
    at("2026-07-20T03:30:00Z");
    expect(clock.now().toISOString()).toBe("2026-07-20T03:30:00.000Z");
    expect(clock.nowISO()).toBe("2026-07-20T03:30:00.000Z");
    vi.setSystemTime(new Date("2026-07-20T11:00:00Z"));
    expect(clock.now().getTime()).toBe(Date.parse("2026-07-20T11:00:00Z"));
  });
});
