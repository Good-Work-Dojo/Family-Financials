// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { pointerWords } from "./pointerWords";

describe("pointerWords", () => {
  it("a finger on a phone taps, is told about the search button and pinches", () => {
    expect(pointerWords(true, true)).toEqual({ touch: true, click: "tap", Click: "Tap", searchButton: true, search: "search",
      zoom: "Pinch the chart to zoom." });
  });
  it("a finger on a wider screen taps but has no 🔍 button to be told about: the palette is ⌘K", () => {
    expect(pointerWords(true, false)).toMatchObject({ click: "tap", searchButton: false, search: "⌘K" });
  });
  it("a mouse clicks, has ⌘K and the wheel, whatever the width", () => {
    for (const phone of [false, true]) {
      expect(pointerWords(false, phone)).toEqual({ touch: false, click: "click", Click: "Click", searchButton: false, search: "⌘K",
        zoom: "Scroll on the chart to zoom." });
    }
  });
});
