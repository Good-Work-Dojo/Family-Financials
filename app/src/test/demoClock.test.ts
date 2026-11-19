// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// The dashboard's test process runs in the zone FF_TEST_TZ names when vitest starts, UTC when it is unset, whatever the
// machine's zone (vite.config.ts `test.env.TZ`), so the "today" a page reads from the frozen ingest instant (a local
// date, test/demoClock.ts) is the same day on every machine. The gate's zone runs include this file, so a run whose
// zone did not take fails here instead of passing in UTC. One test either way: the gate fails a skipped one.
import { expect, it } from "vitest";

const asked = process.env.FF_TEST_TZ || "UTC";

it("the test process runs in the zone asked for (UTC, offset 0 in January and in July, when none is)", () => {
  expect(Intl.DateTimeFormat().resolvedOptions().timeZone).toBe(asked);
  if (!process.env.FF_TEST_TZ) {
    expect(new Date(2026, 0, 15, 12).getTimezoneOffset(), "January").toBe(0);
    expect(new Date(2026, 6, 15, 12).getTimezoneOffset(), "July").toBe(0);
  }
});
