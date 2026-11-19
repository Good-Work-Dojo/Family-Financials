// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EXPORT_FILES, type ExportFile } from "../data/types";

const missing = vi.hoisted(() => ({ files: new Set<string>() }));
vi.mock("../data/DataContext", () => ({ useData: () => ({ missing: missing.files }) }));

import RequireData, { EXPORT_WORDS } from "./RequireData";

afterEach(() => { cleanup(); missing.files = new Set(); });

describe("RequireData", () => {
  it("renders its children when every export loaded", () => {
    render(<RequireData files={["vendors.json"] as ExportFile[]}><p>body</p></RequireData>);
    expect(screen.queryByText("body") !== null).toBe(true);
    expect(screen.queryByTestId("missing-data") === null).toBe(true);
  });
  it("says in words what could not be loaded and what to do, and names no file", () => {
    missing.files = new Set(["vendors.json"]);
    render(<RequireData files={["vendors.json"] as ExportFile[]} what="The learned-rules list"><p>body</p></RequireData>);
    const card = screen.getByTestId("missing-data");
    expect(card.textContent).toBe("The learned-rules list is unavailable — the vendor list could not be loaded. An ingest "
      + "may still be writing it, or the last ingest failed: see the quality report on the Data page, re-run the ingest if "
      + "it failed, and reload.");
    expect(screen.queryByText("body") === null).toBe(true);
    const link = card.querySelector("a");
    expect(link?.getAttribute("href")).toBe("#/data");
    expect(card.textContent).not.toMatch(/\.json/);
  });
  it("names two missing exports together, in words", () => {
    missing.files = new Set(["quality_report.json", "open_questions.json"]);
    render(<RequireData files={["quality_report.json", "open_questions.json"] as ExportFile[]} what="The data-quality panel">
      <p>body</p></RequireData>);
    const text = screen.getByTestId("missing-data").textContent ?? "";
    expect(text).toMatch(/^The data-quality panel is unavailable — the quality report and the open questions could not be loaded\. An ingest may still be writing them,/);
    // the quality report is what did not load: the card sends the reader to the Data page, never to the missing report
    expect(text).toMatch(/or the last ingest failed: check the Data page, re-run the ingest if it failed, and reload\.$/);
    expect(text).not.toMatch(/see the quality report/);
  });
  it("has words for every export", () => {
    expect(Object.keys(EXPORT_WORDS).sort()).toEqual([...EXPORT_FILES].sort());
    expect(Object.values(EXPORT_WORDS).every((w) => /^the [a-z -]+$/.test(w) && !w.includes(".json"))).toBe(true);
  });
});
