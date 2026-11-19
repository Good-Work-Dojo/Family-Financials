// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { describe, expect, it } from "vitest";
import { OWNER_PALETTE, ownerColor, STREAM_PALETTE, streamColor, UNKNOWN_COLOR } from "./tokens";

// the export's owner order: adults, the joint label, children
const OWNERS = ["Alex", "Sam", "Joint", "Robin", "Jamie"];

describe("ownerColor", () => {
  it("gives each owner the palette entry at its index in the owner list", () => {
    expect(OWNERS.map((o) => ownerColor(o, OWNERS))).toEqual(OWNER_PALETTE.slice(0, OWNERS.length));
  });
  it("follows the list's order, not the name: the same name moves with its place", () => {
    expect(ownerColor("Alex", ["Sam", "Alex"])).toBe(OWNER_PALETTE[1]);
    expect(ownerColor("Alex", ["Alex", "Sam"])).toBe(OWNER_PALETTE[0]);
  });
  it("cycles past the palette's end", () => {
    const many = Array.from({ length: OWNER_PALETTE.length + 2 }, (_, i) => `Owner ${i}`);
    expect(ownerColor(many[OWNER_PALETTE.length - 1], many)).toBe(OWNER_PALETTE[OWNER_PALETTE.length - 1]);
    expect(ownerColor(many[OWNER_PALETTE.length], many)).toBe(OWNER_PALETTE[0]);
    expect(ownerColor(many[OWNER_PALETTE.length + 1], many)).toBe(OWNER_PALETTE[1]);
  });
  it("a name not in the list gets the neutral grey, which no owner in the list ever gets", () => {
    expect(ownerColor("Pat", OWNERS)).toBe(UNKNOWN_COLOR);
    expect(ownerColor("Alex", [])).toBe(UNKNOWN_COLOR);
    expect(OWNER_PALETTE).not.toContain(UNKNOWN_COLOR);
  });
  it("is stable for the same list: a repeat call, another call order or a copy of the list gives the same colours", () => {
    const first = OWNERS.map((o) => ownerColor(o, OWNERS));
    const reversed = [...OWNERS].reverse().map((o) => ownerColor(o, [...OWNERS])).reverse();
    expect(reversed).toEqual(first);
    expect(OWNERS.map((o) => ownerColor(o, OWNERS))).toEqual(first);
  });
});

describe("OWNER_PALETTE", () => {
  it("starts with the five colours the dashboard has always drawn its owners in, in owner order", () => {
    expect(OWNER_PALETTE.slice(0, 5)).toEqual(["#3b82f6", "#8b5cf6", "#14b8a6", "#f59e0b", "#ec4899"]);
  });
  it("has room for a larger household before it cycles, every entry distinct", () => {
    expect(OWNER_PALETTE.length).toBeGreaterThanOrEqual(8);
    expect(new Set(OWNER_PALETTE).size).toBe(OWNER_PALETTE.length);
  });
});

describe("streamColor", () => {
  it("gives the stream at each position its palette entry, every entry distinct, and cycles past the end", () => {
    expect(new Set(STREAM_PALETTE).size).toBe(STREAM_PALETTE.length);
    expect(STREAM_PALETTE.length).toBeGreaterThanOrEqual(5);
    expect(STREAM_PALETTE.map((_, i) => streamColor(i))).toEqual(STREAM_PALETTE);
    expect(streamColor(STREAM_PALETTE.length)).toBe(STREAM_PALETTE[0]);
    expect(streamColor(STREAM_PALETTE.length + 1)).toBe(STREAM_PALETTE[1]);
  });
});
