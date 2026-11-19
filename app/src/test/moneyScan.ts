// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Test support: the privacy net's money scanners, shared by the suites that mount the app on exports (the smoke suite's
// privacy net and the matrix's privacy check). They are written independently of lib/privacy.ts, with their own
// patterns: `textHits` scans the text and attributes under an element for amount-shaped text, `optionHits` calls a
// chart option's formatters with integer samples, and `STATIC_OK` blanks the static prose that states a rule or counts
// rows. A hit is redacted (digits become #), so a failure names the pattern, never an amount. `STATIC_SEEN` records which
// `STATIC_OK` alternative matched in a recorded scan (one test file's module state). docs/design/privacy.md.
// Nothing in the application imports this module.

// numbers become # so a failure names the pattern, never an amount
const redact = (s: string) => s.replace(/\d/g, "#");
// Amount-shaped text: a currency mark ($ EUR GBP USD) before a number; a grouped number ("1,234", "-1,234.56"); a
// two-decimal number ("12.50"). A number followed by "%" is a ratio, never an amount.
export const AMOUNTS = [
  /.{0,24}(?:[$€£]|\b(?:USD|EUR|GBP)(?![A-Za-z])\s?)\s?[\d.]\d*.{0,8}/g,
  /.{0,24}(?<![\w.,])[-−]?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?!\w|,\d|\.\d|\s?%).{0,8}/g,
  /.{0,24}(?<![\w.,])[-−]?\d+\.\d\d(?!\w|\d|\.\d|\s?%).{0,8}/g,
];
// Static prose that states a rule or a threshold, or counts rows - hard-coded guidance, not the household's money.
// Masking it would only make the text unreadable. A NEW static dollar figure has to be listed here on purpose, and an
// entry must match on some scanned route or surface (the net records each match; its last test refuses a dead one).
/** The nouns the app prints after a GROUPED count ("6,190 transactions": `countOf`, or a `toLocaleString` before the
 *  word), each as a regex fragment: a household with a thousand of one would otherwise read as an amount. A noun the
 *  app prints only after an ungrouped number can never match and is not listed; test/moneyScan.test.ts finds where the
 *  tree prints each one and refuses a noun it cannot find. */
export const COUNTED_NOUNS = [
  "transactions?",        // "6,190 transactions"; "Export CSV: 1,151 rows (1,140 transactions)"
  "rows?",                // "Export CSV: 1,151 rows", Data's "1,413 rows" read from a file
  "accounts?",            // "of the assets reported so far - 1,000 accounts not yet reported"
  "snapshots?",           // Home's "1,024 snapshots since 2031"
  "checks?",              // Data's "Quality report - 3 warnings of 1,050 checks", "all 1,050 checks"
  "open questions?",      // the sidebar's and Home's "1,234 open questions"
  "warnings?",            // Home's "1,050 warnings"
  "earlier month-ends?",  // Data's "1,000 earlier month-ends are left out"
  "left, smaller amounts", // Review's "(1,234 left, smaller amounts)": the whole phrase, so "$1,234 left" is no count
];
// a count is never a number that follows a currency sign: "$1,234 transactions" is an amount, whatever word follows
const NOT_MONEY = String.raw`(?<![$€£]\s?)`;
export const STATIC_OK_PARTS = [
  String.raw`latest interpolated \$100K`,                                    // the net-worth milestone step
  // a COUNT, by its noun
  String.raw`${NOT_MONEY}\b\d{1,3}(?:,\d{3})+ (?:${COUNTED_NOUNS.join("|")})\b`,
  String.raw`${NOT_MONEY}\b\d{1,3}(?:,\d{3})+(?= of \d{1,3}(?:,\d{3})+ transactions)`,    // "1,151 of 6,190 transactions"
];
export const STATIC_OK = new RegExp(STATIC_OK_PARTS.join("|"), "g");
const STATIC_ALTERNATIVES = STATIC_OK_PARTS.map((p) => new RegExp(p));
/** the alternatives (by index) that matched the text of some scanned route or surface */
export const STATIC_SEEN = new Set<number>();
/** `s` with its static prose blanked out; on a route or surface (`record`), each alternative that matched is noted */
const allowStatic = (s: string, record: boolean): string => {
  if (record) STATIC_ALTERNATIVES.forEach((re, i) => { if (re.test(s)) STATIC_SEEN.add(i); });
  return s.replace(STATIC_OK, " ");
};
// The text a reader sees, in two views. (1) EACH ELEMENT'S OWN text (its direct text nodes joined), scanned on its own:
// `textContent` glues the end of one element to the start of the next ("net 1,234.56" + "Uncategorized" reads
// "...56Uncategorized", so the digit-then-letter lookahead skips it - and the last data-gap row always runs into the next
// card). (2) The whole `textContent`, so an amount split across sibling elements ("$" in one, "123" in the next) still joins.
// a Qty / Quantity column counts UNITS of a holding (shares, coins): a number, not dollars
const inQuantityColumn = (n: Node): boolean => {
  if (!(n instanceof HTMLTableCellElement) || !n.parentElement) return false;
  const head = n.closest("table")?.querySelectorAll("thead th")[[...n.parentElement.children].indexOf(n)];
  return /^(qty|quantity)$/i.test(head?.textContent?.trim() ?? "");
};
export const textViews = (root: HTMLElement): string[] => {
  const views = [root.textContent ?? ""];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
  for (let n: Node | null = walker.currentNode; n; n = walker.nextNode()) {
    if (inQuantityColumn(n)) continue;
    const own = [...n.childNodes].filter((c) => c.nodeType === Node.TEXT_NODE).map((c) => c.textContent ?? "").join("");
    if (own.trim()) views.push(own);
  }
  return views;
};
// A box's placeholder or value is a whole number with no "$" or grouping ("1234" - a budget's automatic baseline
// as its placeholder): the text patterns need a mark to call a number money, but a number IN a box is a figure the
// page shows. So a box that shows a whole number of 100 or more is an amount, unless the label says "%" (above)
// or the box is a slider (its whole-number value is the thumb's position, blurred under privacy mode; what it says out
// loud is its aria-valuetext, scanned above) or a date. Ages, years of school, nights a month stay under 100.
const NOT_AMOUNT_BOXES = new Set(["range", "date", "month", "week", "time", "datetime-local", "checkbox", "radio", "hidden"]);
const BOX_AMOUNT = /^\s*[-\u2212]?\$?\s?(?:\d{1,3}(?:,\d{3})+|\d{3,})(?:\.\d+)?\s*$/;
const boxShowsAmount = (el: HTMLInputElement, v: string): boolean => !NOT_AMOUNT_BOXES.has(el.type) && BOX_AMOUNT.test(v);
// A value with ONE decimal ("2746.4": a two-decimal amount whose trailing zero a number drops) is an amount in any box, a
// slider's too: a slider's value attribute is the figure the page seeded it with, not the position its step snaps the
// thumb to. A rate's box sits in a label that says "%" (above), so a "6.5" there is never read; "6.125" and "7" have
// no single decimal.
const ONE_DECIMAL = /^\s*[-\u2212]?\d+\.\d\s*$/;
export const textHits = (root: HTMLElement, record = false): string[] => {
  const out: string[] = [];
  for (const view of textViews(root)) {
    const text = allowStatic(view, record);
    for (const re of AMOUNTS) for (const m of text.matchAll(re)) out.push(`text ...${redact(m[0])}`);
  }
  for (const el of root.querySelectorAll("[title],[aria-label],[placeholder],[aria-valuetext],[alt],input")) {
    // a box inside a label that carries a "%" is a RATE (the projection defaults' seeds: 2.75): not an amount
    if (el.tagName === "INPUT" && el.closest("label")?.textContent?.includes("%")) continue;
    for (const a of ["title", "aria-label", "placeholder", "aria-valuetext", "alt", "value"]) {
      const attr = el.getAttribute(a) ?? (a === "value" ? (el as HTMLInputElement).value : null);
      const v = attr == null ? null : allowStatic(attr, record);    // an element that is no input has no `value`
      if (v && AMOUNTS.some((re) => new RegExp(re.source).test(v))) out.push(`${el.tagName.toLowerCase()}[${a}] ${redact(v)}`);
      else if (v && el.tagName === "INPUT" && (a === "placeholder" || a === "value") && boxShowsAmount(el as HTMLInputElement, v)) {
        out.push(`input[${a}] ${redact(v)}`);
      } else if (v && el.tagName === "INPUT" && a === "value" && ONE_DECIMAL.test(v)) out.push(`input[value] ${redact(v)}`);
    }
  }
  return out;
};
// A chart prints what its formatters return. The sample is an INTEGER so rounded forms are caught too: "$123,456",
// "$123K", "$123.5K", "123456" all contain 123 (a percentage is the one exception: it may print the number).
const SAMPLE = 123456;
// Only a percentage TOKEN is exempt ("12.5%", "123456%"): strip those, then scan what is left - so "$123,456 (12.5%)" is
// still a leak. (A blanket `!/%/` would let any string that mentions a percent through.)
export const PERCENT_TOKEN = /[-+\u2212]?\d[\d,]*(?:\.\d+)?\s?%/g;
export const raw = (s: unknown) => {
  if (typeof s !== "string") return false;
  const rest = s.replace(PERCENT_TOKEN, "");
  return /123/.test(rest) || AMOUNTS.some((re) => new RegExp(re.source).test(rest));
};
// the shapes ECharts hands a formatter: a category label (a month axis), a bare value, a point, a list of points, a point whose value is [date, v] or [x, y, v]
const point = { value: SAMPLE, name: "n", dataIndex: 0, seriesName: "s", marker: "", percent: 12.5, data: SAMPLE, color: "#000", componentType: "series" };
const SHAPES: unknown[] = ["2026-01", SAMPLE, point, [point], { ...point, value: ["2026-01-01", SAMPLE, SAMPLE], data: ["2026-01-01", SAMPLE, SAMPLE] },
  [{ ...point, value: ["2026-01-01", SAMPLE, SAMPLE] }]];
// Planning -> Home's x axis is a YEAR axis (2026, 2031...): printing the number is right
const YEAR_AXES = new Set(["/planning/home .xAxis.axisLabel.formatter"]);
export const optionHits = (route: string, option: any, stats: { formatters: number }): string[] => {
  const out: string[] = [];
  const seen = new Set<object>();
  const walk = (o: any, path: string) => {
    if (!o || typeof o !== "object" || seen.has(o)) return;
    seen.add(o);
    for (const [k, v] of Object.entries(o)) {
      const at = `${path}.${k}`;
      if ((k === "formatter" || k === "valueFormatter") && typeof v === "function") {
        stats.formatters++;
        let threw = 0;
        let leaked = false;
        // a legend's formatter receives a category NAME and echoes it (with a share): hand it a name, not a number
        for (const arg of at.endsWith(".legend.formatter") ? ["Cash"] : SHAPES) {
          let res: unknown;
          try { res = (v as (...a: unknown[]) => unknown)(arg, 0); } catch { threw++; continue; }
          if (raw(res) && !YEAR_AXES.has(`${route} ${at}`)) leaked = true;
        }
        if (leaked) out.push(`option ${at}() prints a raw amount`);
        // a formatter that cannot be evaluated is not a pass: it fails on every shape, so nothing was checked
        if (threw === (at.endsWith(".legend.formatter") ? 1 : SHAPES.length)) out.push(`option ${at}() throws on every sample shape (nothing was checked)`);
      } else if (k === "formatter" && typeof v === "string" && /\{(c|@[^}]*)\}/.test(v)) {
        out.push(`option ${at} is a raw ${v} template`);
      } else if ((k === "markLine" || k === "markPoint") && v && typeof v === "object") {
        // a mark's label prints its value unless it has a formatter (or is off)
        const label = (v as any).label;
        if (label?.show !== false && label?.formatter === undefined) out.push(`option${at} label prints its raw value`);
        walk(v, at);
      } else if (k === "label" && v && typeof v === "object" && /series|data/.test(path)) {
        // a series (or data point) label that is switched on prints its raw value unless it has a formatter
        if ((v as any).show === true && (v as any).formatter === undefined) out.push(`option${at} prints its raw value`);
        walk(v, at);
      } else if (v && typeof v === "object") walk(v, at);
    }
  };
  walk(option, "");
  // a value axis prints its ticks as numbers: they need a formatter (a function is evaluated above; a "{value}%" template is a ratio)
  for (const axis of [option?.xAxis, option?.yAxis].flat().filter(Boolean)) {
    if (axis.type !== "value" || axis.show === false || axis.axisLabel?.show === false) continue;
    const f = axis.axisLabel?.formatter;
    if (!(typeof f === "function" || (typeof f === "string" && f.includes("%")))) out.push("option value axis prints raw ticks");
  }
  // a continuous visualMap prints its min and max unless it has a formatter; a default tooltip prints raw values
  for (const vm of [option?.visualMap].flat().filter(Boolean)) {
    if (typeof vm.formatter !== "function" && vm.type !== "piecewise") out.push("option.visualMap prints raw min/max");
  }
  const tip = option?.tooltip;
  if (tip && tip.show !== false && tip.formatter === undefined && tip.valueFormatter === undefined) out.push("option.tooltip prints raw values");
  return out;
};
