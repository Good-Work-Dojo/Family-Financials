// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { CODE_PILL, EMPTY_LINE } from "../components/NoData";
import { LINE_KEY } from "../components/ui";
import { withoutComments } from "../test/sourceText";
import type { CategorySource } from "../data/types";
import { sourceChip } from "./categorySource";
import { contrastRatio, luminance, over } from "./contrast";
import { CHART_GAIN, CHART_MUTED, CHART_RAMP, CHART_SURFACE, CHART_TOTAL_RATE, setChartTheme } from "./echarts";
import { NEUTRAL_MARK } from "./tokens";

// The two themes as index.css declares them: the dark one is Tailwind's own zinc ramp with the overrides in `@theme`,
// the light one the ramp `.light` remaps. A card is bg-zinc-900/60 over the page.
const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");
const css = readFileSync(join(SRC, "index.css"), "utf8");
const lightBlock = /\.light\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? "";
const themeBlock = /@theme\s*\{([^}]*)\}/.exec(css)?.[1] ?? "";
const declared = (block: string, name: string) => new RegExp(`--color-${name}:\\s*(#[0-9a-fA-F]{6})`).exec(block)?.[1];
const lightVar = (name: string) => declared(lightBlock, name) as string;
/** Tailwind's own zinc, which the dark theme uses where `@theme` does not override a step */
const TAILWIND_ZINC: Record<string, string> = { "zinc-300": "#d4d4d8", "zinc-400": "#a1a1aa", "zinc-500": "#71717a",
  "zinc-900": "#18181b", "zinc-950": "#09090b" };
const darkVar = (name: string) => declared(themeBlock, name) ?? TAILWIND_ZINC[name];
const ratio = contrastRatio;
const PAGE = { dark: darkVar("zinc-950"), light: lightVar("zinc-950") };
const CARD = { dark: over(darkVar("zinc-900"), PAGE.dark, 0.6), light: over(lightVar("zinc-900"), PAGE.light, 0.6) };

describe("luminance", () => {
  it("is 0 for black and 1 for white (black on white is the WCAG 21:1), short hex included", () => {
    expect(luminance("#000000")).toBe(0);
    expect(luminance("#ffffff")).toBeCloseTo(1, 10);
    expect((luminance("#fff") + 0.05) / (luminance("#000") + 0.05)).toBeCloseTo(21, 10);
  });
  it("linearises each channel before weighting it: mid-grey is about 0.216, pure red is the red weight", () => {
    expect(luminance("#808080")).toBeCloseTo(0.2159, 4);
    expect(luminance("#ff0000")).toBeCloseTo(0.2126, 10);
  });
});

// Fine print (footnotes, "based on:" lines, hints) is ONE token, `text-faint` (--color-faint in index.css), and it is readable
// on both surfaces it sits on in both themes. It was `text-zinc-600`: about 2.4:1 on a dark card, 2.5:1 on a light one.
describe("text is readable on a card and on the page, in both themes", () => {
  it("both themes define the fine-print token", () => {
    expect(darkVar("faint"), "--color-faint in @theme").toMatch(/^#/);
    expect(lightVar("faint"), "--color-faint in .light").toMatch(/^#/);
  });
  it("fine print is at least 4.5:1 on the card and on the page, dark and light", () => {
    for (const theme of ["dark", "light"] as const) {
      const fg = theme === "dark" ? darkVar("faint") : lightVar("faint");
      for (const [where, bg] of [["card", CARD[theme]], ["page", PAGE[theme]]]) {
        expect(ratio(fg, bg), `${theme} ${where}: ${fg} on ${bg}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });
  // zinc-500 is the muted text of about 160 places (table heads, secondary lines); Tailwind's own #71717a was about 3.9:1
  // on a dark card and 4.4:1 on the light page, so each theme declares its own shade
  it("muted text (zinc-500) is at least 4.5:1 on the card and on the page, in both themes", () => {
    expect(declared(themeBlock, "zinc-500"), "the dark theme declares the step").toMatch(/^#/);
    expect(declared(lightBlock, "zinc-500"), "the light theme declares the step").toMatch(/^#/);
    for (const theme of ["dark", "light"] as const) {
      const fg = theme === "dark" ? darkVar("zinc-500") : lightVar("zinc-500");
      for (const [where, bg] of [["card", CARD[theme]], ["page", PAGE[theme]]]) {
        expect(ratio(fg, bg), `${theme} ${where}: ${fg} on ${bg}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });
  it("no source file styles text with zinc-600 any more: fine print has the one token", () => {
    const files = (dir: string): string[] => readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
      e.isDirectory() ? files(join(dir, e.name))
      : /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [join(dir, e.name)] : []);
    const all = files(SRC);
    expect(all.length).toBeGreaterThan(50);
    const hits = all.filter((f) => /text-zinc-600/.test(readFileSync(f, "utf8"))).map((f) => f.slice(SRC.length + 1));
    expect(hits).toEqual([]);
    expect(all.filter((f) => /text-faint/.test(readFileSync(f, "utf8"))).length, "the token is in use").toBeGreaterThan(20);
  });

  // The code pill carries the config keys every empty state names. Its dark wash (bg-black/40) over a white card is a mid
  // grey that left its text at about 1.6:1, so the light theme gives it a background and a text colour of its own.
  it("the code pill is at least 4.5:1 in the light theme, and the dark theme keeps its wash", () => {
    expect(CODE_PILL.startsWith("rounded bg-black/40 px-1 "), "the dark theme's classes are unchanged").toBe(true);
    const bg = /(?:^| )light:bg-(zinc-\d+)(?: |$)/.exec(CODE_PILL)?.[1];
    const fg = /(?:^| )light:text-(zinc-\d+)(?: |$)/.exec(CODE_PILL)?.[1];
    expect(bg !== undefined && fg !== undefined, "the light theme names both colours from the remapped ramp").toBe(true);
    const [pill, ink] = [lightVar(bg as string), lightVar(fg as string)];
    expect(ratio(ink, pill), `${ink} on ${pill}`).toBeGreaterThanOrEqual(4.5);
    // and the pill still stands out from the card it sits on
    expect(pill, "the pill is not the card's own colour").not.toBe(lightVar("zinc-900"));
  });

  /** the zinc step a class list colours its text with outside the light theme */
  const textOf = (classes: string) => /(?:^| )text-(zinc-\d+)(?: |$)/.exec(classes)?.[1] as string;
  // In the dark theme the pill took the line's own text colour, and a muted line's grey on the wash was about 4.1:1.
  it("in the dark theme the code pill's own text is at least 4.5:1 on its wash over a card", () => {
    const wash = over("#000000", CARD.dark, 0.4);
    const ink = darkVar(textOf(CODE_PILL));
    expect(ink, "the pill names a text colour of its own").toBeDefined();
    expect(ratio(ink, wash), `${ink} on ${wash}`).toBeGreaterThanOrEqual(4.5);
  });
  // An empty-state line and a key-fact list's key (Data's facts, an account's header) were zinc-500: about 3.9:1 on a dark card.
  it("an empty-state line and a key-fact list's key are at least 4.5:1 on a card in both themes", () => {
    for (const [what, classes] of [["the empty-state line", EMPTY_LINE], ["a list's key", LINE_KEY]] as const) {
      const step = textOf(classes);
      expect(ratio(darkVar(step), CARD.dark), `${what}, dark: ${darkVar(step)}`).toBeGreaterThanOrEqual(4.5);
      expect(ratio(lightVar(step), CARD.light), `${what}, light: ${lightVar(step)}`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("code prints through <Code> alone: no source file has a <code> element or a copy of the pill's dark wash", () => {
    const files = (dir: string): string[] => readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
      e.isDirectory() ? files(join(dir, e.name))
      : /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [join(dir, e.name)] : []);
    // the shared element's own file is the one place a <code> is written; comments are not markup
    const others = files(SRC).filter((f) => !f.endsWith(join("components", "NoData.tsx")))
      .map((f) => [f.slice(SRC.length + 1), withoutComments(readFileSync(f, "utf8"))] as const);
    expect(others.filter(([, src]) => bareCode(src)).map(([f]) => f), "a bare <code>").toEqual([]);
    expect(others.filter(([, src]) => /bg-black\/40 px-1/.test(src)).map(([f]) => f), "a copy of the dark wash").toEqual([]);
    expect(others.length).toBeGreaterThan(50);
  });
  it("the scan reads past a URL: a // inside a string ends no line, and a <code> in a comment is no markup", () => {
    expect(bareCode(withoutComments('const site = "https://example.org"; const el = <code>x</code>;')), "after a URL").toBe(true);
    expect(bareCode(withoutComments("const n = 1; // a <code> in a line comment")), "in a line comment").toBe(false);
    expect(bareCode(withoutComments("// a <code> on a comment line of its own")), "a whole comment line").toBe(false);
    expect(bareCode(withoutComments("{/* a <code> in a JSX comment */}")), "in a block comment").toBe(false);
  });
});

/** the colour at `t` (0 to 1) along a ramp, as ECharts' visualMap interpolates its stops: channel by channel, evenly
 *  spaced, each channel rounded */
function rampAt(stops: readonly string[], t: number): string {
  const v = t * (stops.length - 1);
  const [lo, hi] = [Math.floor(v), Math.ceil(v)];
  const ch = (c: string) => [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16));
  const [a, b] = [ch(stops[lo]), ch(stops[hi])];
  return "#" + a.map((x, i) => Math.round(x + (b[i] - x) * (v - lo)).toString(16).padStart(2, "0")).join("");
}

// Chart colours are canvas tokens (lib/echarts.ts setChartTheme), read here in each theme against the theme's own card.
describe("chart marks stand out from what they sit on (3:1), in both themes", () => {
  afterEach(() => setChartTheme("dark"));
  for (const theme of ["dark", "light"] as const) {
    it(`${theme}: the calendar's ramp, sampled at eleven points as ECharts interpolates it, against an empty day's cell`, () => {
      setChartTheme(theme);
      for (let k = 0; k <= 10; k++) {
        const c = rampAt(CHART_RAMP, k / 10);
        expect(ratio(c, CHART_SURFACE), `${theme} at ${k * 10}%: ${c} on ${CHART_SURFACE}`).toBeGreaterThanOrEqual(3);
      }
    });
    it(`${theme}: the gain green, the muted grey and the neutral mark on the card`, () => {
      setChartTheme(theme);
      for (const [what, c] of [["gain", CHART_GAIN], ["muted", CHART_MUTED], ["neutral", NEUTRAL_MARK],
        ["total savings rate", CHART_TOTAL_RATE]] as const) {
        expect(ratio(c, CARD[theme]), `${theme} ${what}: ${c} on ${CARD[theme]}`).toBeGreaterThanOrEqual(3);
      }
    });
  }
  it("the ramp sampler interpolates evenly between stops, rounding each channel", () => {
    expect(rampAt(["#000000", "#ffffff"], 0.5)).toBe("#808080");
    expect(rampAt(["#000000", "#ff0000", "#ffffff"], 0.75)).toBe("#ff8080");
  });
});

/** an `oklch(L% C H)` colour (Tailwind's own palette) as #rrggbb: OKLab, then linear sRGB, then gamma, each channel
 *  clamped and rounded */
function oklchHex(value: string): string {
  const m = /oklch\(\s*([\d.]+)%\s+([\d.]+)\s+([\d.]+)\s*\)/.exec(value);
  if (!m) throw new Error(`not an oklch colour: ${value}`);
  const [L, C, H] = [Number(m[1]) / 100, Number(m[2]), (Number(m[3]) * Math.PI) / 180];
  const [a, b] = [C * Math.cos(H), C * Math.sin(H)];
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const mm = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const lin = [4.0767416621 * l - 3.3077115913 * mm + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * mm - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * mm + 1.707614701 * s];
  const gamma = (v: number) => (v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055);
  return "#" + lin.map((v) => Math.round(Math.min(1, Math.max(0, gamma(v))) * 255).toString(16).padStart(2, "0")).join("");
}

// A provenance chip (lib/categorySource.ts) is its ink on its tint: the letters are text, at least 4.5:1 on the tint, and
// the chip has no border, so its ink is also the mark that tells it from the card it sits in, at least 3:1 there. The dark
// theme draws Tailwind's own colours (read from its theme file), the light one the `.light` remap.
describe("provenance chips are readable and stand out, in both themes", () => {
  const tailwind = readFileSync(join(SRC, "..", "node_modules", "tailwindcss", "theme.css"), "utf8");
  const tailwindVar = (name: string) => {
    const v = new RegExp(`--color-${name}:\\s*([^;]+);`).exec(tailwind)?.[1];
    if (v === undefined) throw new Error(`--color-${name} is not in Tailwind's theme`);
    return oklchHex(v);
  };
  const SOURCES: CategorySource[] = ["internal", "type_default", "parser", "vendor_rule", "learned", "keyword",
    "bank_label", "fallback", "answer", "override", "split", "ai"];
  const chips = SOURCES.map(sourceChip).filter((c) => c !== null);
  it("the AI chip is one of them", () => {
    expect(chips.map((c) => c.label)).toEqual(["learned", "split", "AI"]);
  });
  for (const theme of ["dark", "light"] as const) {
    it(`${theme}: each chip's text is at least 4.5:1 on its tint, and its mark at least 3:1 on the card`, () => {
      for (const chip of chips) {
        const hue = /bg-([a-z]+)-950/.exec(chip.cls)?.[1] as string;
        const at = (step: number) => (theme === "dark" ? tailwindVar(`${hue}-${step}`) : lightVar(`${hue}-${step}`));
        const [tint, ink] = [at(950), at(300)];
        expect(tint, `${theme} ${chip.label}: the tint is declared`).toMatch(/^#[0-9a-f]{6}$/i);
        expect(ink, `${theme} ${chip.label}: the ink is declared`).toMatch(/^#[0-9a-f]{6}$/i);
        expect(ratio(ink, tint), `${theme} ${chip.label} text: ${ink} on ${tint}`).toBeGreaterThanOrEqual(4.5);
        expect(ratio(ink, CARD[theme]), `${theme} ${chip.label} mark: ${ink} on ${CARD[theme]}`).toBeGreaterThanOrEqual(3);
      }
    });
  }
  it("reads Tailwind's oklch colours as sRGB: white, black and a known zinc step", () => {
    expect(oklchHex("oklch(100% 0 0)")).toBe("#ffffff");
    expect(oklchHex("oklch(0% 0 0)")).toBe("#000000");
    expect(tailwindVar("zinc-900")).toBe(TAILWIND_ZINC["zinc-900"]);
  });
});

describe("contrastRatio and over", () => {
  it("black on white is 21:1 either way round, a colour on itself 1:1", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 10);
    expect(contrastRatio("#ffffff", "#000000")).toBeCloseTo(21, 10);
    expect(contrastRatio("#3b82f6", "#3b82f6")).toBe(1);
  });
  it("composites a translucent colour over its background channel by channel", () => {
    expect(over("#18181b", "#09090b", 0.6)).toBe("#121215");
    expect(over("#000000", "#ffffff", 0.4)).toBe("#999999");
  });
});

/** a <code> element written by hand */
const bareCode = (src: string) => /<code[\s>]/.test(src);
