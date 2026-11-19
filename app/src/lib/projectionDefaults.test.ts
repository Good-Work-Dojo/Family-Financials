// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { isProjectionDefault, middleReturn, projectionSource } from "./projectionDefaults";

describe("middleReturn", () => {
  it("is the middle of the return scenarios, whatever their order; the lower middle of an even count", () => {
    expect(middleReturn([5, 7, 9])).toBe(7);
    expect(middleReturn([8, 4, 6])).toBe(6);
    expect(middleReturn([4, 6, 8, 10])).toBe(6);
    expect(middleReturn([5.5])).toBe(5.5);
  });
  it("an empty list states no return: none", () => {
    expect(middleReturn([])).toBe(0);
  });
});

describe("projectionSource", () => {
  it("a key saved in the app is the family's; else one the configuration states is the config's; else the default", () => {
    expect(projectionSource(["withdrawal_rate_pct"], { withdrawal_rate_pct: 3.5 }, "withdrawal_rate_pct")).toBe("app");
    expect(projectionSource(["withdrawal_rate_pct"], {}, "withdrawal_rate_pct")).toBe("config");
    expect(projectionSource([], {}, "withdrawal_rate_pct")).toBe("default");
    expect(projectionSource([], { inflation_pct: 2 }, "withdrawal_rate_pct"), "another key saved states nothing of this one")
      .toBe("default");
  });
  it("a saved value that is no finite number states nothing", () => {
    expect(projectionSource([], { withdrawal_rate_pct: Number.NaN }, "withdrawal_rate_pct")).toBe("default");
  });
  it("isProjectionDefault is the default case alone", () => {
    expect(isProjectionDefault([], {}, "return_scenarios_pct")).toBe(true);
    expect(isProjectionDefault(["return_scenarios_pct"], {}, "return_scenarios_pct")).toBe(false);
  });
});
