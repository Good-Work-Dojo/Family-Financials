// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Test support: contrast arithmetic, the WCAG relative luminance a contrast ratio between two colours is computed from
// (lib/contrast.test.ts holds the palette to its ratios). Nothing in the application imports it.

const hex = (c: string): [number, number, number] => {
  const h = c.replace("#", "");
  const full = h.length === 3 ? h.split("").map((x) => x + x).join("") : h;
  return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16)) as [number, number, number];
};

/** WCAG relative luminance of a #rrggbb color */
export function luminance(c: string): number {
  const [r, g, b] = hex(c).map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** the WCAG contrast ratio of two #rrggbb colours (1 to 21; text wants 4.5, a chart mark or a boundary 3) */
export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** `fg` drawn at `alpha` over `bg`, as #rrggbb: a translucent fill as the eye sees it on its background */
export function over(fg: string, bg: string, alpha: number): string {
  const [f, b] = [hex(fg), hex(bg)];
  return "#" + f.map((v, i) => Math.round(alpha * v + (1 - alpha) * b[i]).toString(16).padStart(2, "0")).join("");
}
