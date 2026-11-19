// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { DEFAULT_SOURCE_URL, SOURCE_URL, sourceUrlFrom } from "./product";

describe("the source address", () => {
  it("is the repository's when the build names none, or names something that is no web address", () => {
    expect(sourceUrlFrom({})).toBe(DEFAULT_SOURCE_URL);
    for (const given of ["", "  ", "github.com/someone/fork", "javascript:alert(1)", "ftp://example.net/src",
      "https://", "https://exa mple.net/src", 42, null]) {
      expect(sourceUrlFrom({ VITE_SOURCE_URL: given }), String(given)).toBe(DEFAULT_SOURCE_URL);
    }
  });
  it("is a fork's own when its build sets VITE_SOURCE_URL to a web address", () => {
    expect(sourceUrlFrom({ VITE_SOURCE_URL: "https://example.net/someone/fork" })).toBe("https://example.net/someone/fork");
    expect(sourceUrlFrom({ VITE_SOURCE_URL: " https://example.net/fork " })).toBe("https://example.net/fork");
    expect(sourceUrlFrom({ VITE_SOURCE_URL: "http://192.0.2.10:3000/src" })).toBe("http://192.0.2.10:3000/src");
  });
  it("is the repository's in a build that sets nothing, as the tests' is", () => {
    expect(SOURCE_URL).toBe(DEFAULT_SOURCE_URL);
  });
});
