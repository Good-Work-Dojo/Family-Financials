// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { CategorySource } from "../data/types";
import { chipInk, isSourceGroup, SOURCE_GROUPS, sourceChip, sourceGroup } from "./categorySource";
import { luminance } from "../test/contrast";

// every category source the export may carry (etl/models.py CATEGORY_SOURCES, the zod enum in data/schemas.ts)
const ALL: CategorySource[] = ["internal", "type_default", "parser", "vendor_rule", "learned", "keyword",
  "bank_label", "fallback", "answer", "override", "split", "ai"];

describe("sourceChip", () => {
  it("badges learned, split and AI rows and nothing else", () => {
    expect(sourceChip("learned")).toEqual({
      label: "learned", title: "learned from your answers", cls: "bg-purple-950 text-purple-300" });
    expect(sourceChip("split")).toEqual({
      label: "split", title: "split by a vendor rule", cls: "bg-sky-950 text-sky-300" });
    expect(sourceChip("ai")).toEqual({ label: "AI", title: "Filed by AI", cls: "bg-pink-950 text-pink-300" });
    for (const s of ALL.filter((s) => s !== "learned" && s !== "split" && s !== "ai")) expect(sourceChip(s), s).toBeNull();
  });

  it("each chip has a hue no other chip uses", () => {
    const hues = ALL.map(sourceChip).filter((c) => c !== null).map((c) => /bg-([a-z]+)-950/.exec(c.cls)?.[1]);
    expect(hues).toEqual(["purple", "sky", "pink"]);
    expect(new Set(hues).size).toBe(hues.length);
  });

  it("chipInk is the chip's own text-<hue>-300 token (the expanded row says the same provenance in its ink)", () => {
    expect(chipInk(sourceChip("learned")!)).toBe("text-purple-300");
    expect(chipInk(sourceChip("split")!)).toBe("text-sky-300");
    expect(chipInk(sourceChip("ai")!)).toBe("text-pink-300");
    expect(chipInk({ label: "x", title: "x", cls: "bg-zinc-800" })).toBe("");
  });

  // The light theme re-skins by remapping Tailwind's color variables (index.css `.light`), so a chip whose
  // tint/ink pair is not remapped there renders its DARK-theme colors on a white page: a navy pill, ink
  // at ~1.6:1. Reads the SOURCE with node:fs, like capabilities.test.ts.
  it("every chip's bg-*-950 tint and text-*-300 ink is remapped in the light theme, at >= 4.5:1", () => {
    const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "index.css"), "utf8");
    const light = /\.light\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? "";
    expect(light, "the .light block was found").toContain("--color-zinc-950");
    const color = (name: string) => new RegExp(`--color-${name}:\\s*(#[0-9a-fA-F]{6})`).exec(light)?.[1];
    const chips = ALL.map(sourceChip).filter((c) => c !== null);
    expect(chips.length).toBeGreaterThan(2);                                   // learned, split and AI at least
    for (const chip of chips) {
      const [, hue] = /bg-([a-z]+)-950/.exec(chip.cls) ?? [];
      expect(hue, `${chip.label}: a bg-<hue>-950 tint`).toBeTruthy();
      expect(chip.cls, `${chip.label}: ink is the same hue's 300`).toContain(`text-${hue}-300`);
      const [tint, ink] = [color(`${hue}-950`), color(`${hue}-300`)];
      expect(tint, `${chip.label}: --color-${hue}-950 in .light`).toMatch(/^#/);
      expect(ink, `${chip.label}: --color-${hue}-300 in .light`).toMatch(/^#/);
      const [hi, lo] = [luminance(tint as string), luminance(ink as string)].sort((a, b) => b - a);
      expect((hi + 0.05) / (lo + 0.05), `${chip.label} light-theme contrast`).toBeGreaterThanOrEqual(4.5);
    }
  });
});

describe("the sources facet's groups", () => {
  it("are the seven the family reads, in order, each source in exactly one", () => {
    expect(SOURCE_GROUPS.map((g) => g.label)).toEqual(["Your rules", "Learned from your answers", "Your answers",
      "Overrides", "The bank's label", "AI", "Not yet sorted"]);
    const listed = SOURCE_GROUPS.flatMap((g) => [...g.sources] as CategorySource[]);
    expect([...listed].sort()).toEqual([...ALL].sort());
    expect(new Set(listed).size).toBe(listed.length);
  });
  it("sourceGroup files each source under its group: AI alone under AI, the fallback alone under not yet sorted", () => {
    expect(sourceGroup("ai")).toBe("ai");
    expect(sourceGroup("fallback")).toBe("unsorted");
    expect(sourceGroup("learned")).toBe("learned");
    expect(sourceGroup("answer")).toBe("answers");
    expect(sourceGroup("override")).toBe("overrides");
    for (const s of ["vendor_rule", "keyword", "split", "internal", "type_default"] as const) expect(sourceGroup(s), s).toBe("rules");
    for (const s of ["bank_label", "parser"] as const) expect(sourceGroup(s), s).toBe("bank");
    expect(ALL.filter((s) => sourceGroup(s) === "ai")).toEqual(["ai"]);
    expect(ALL.filter((s) => sourceGroup(s) === "unsorted")).toEqual(["fallback"]);
  });
  it("isSourceGroup knows the ids and nothing else", () => {
    for (const g of SOURCE_GROUPS) expect(isSourceGroup(g.id)).toBe(true);
    for (const junk of ["", "AI", "vendor_rule", "fallback", "constructor"]) expect(isSourceGroup(junk), junk).toBe(false);
  });
});
