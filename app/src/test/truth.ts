// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Test support: the demo generator's own statement of the household it wrote (tools/demo, `truth.json`), read by the
// dashboard suites for the names they need: the account whose page they open, the split vendor, the mortgage and the
// brokerage accounts, and a few figures. FF_TRUTH (an absolute path) names the file; without it the file is
// truth.json two directories above the exports (the FF_HOME root a generator run wrote: <root>/data/exports).
// Only the parts the suites read are validated, and every planted handle they read is required, so a household that
// lacks one fails to load with the handle's name instead of a test passing on nothing. The document's `version` must
// be the one this reader is written for (TRUTH_VERSION, tools/demo/truth.py VERSION): a generator that changes the
// shape bumps it, and the suites then refuse the file until this reader follows. Unknown keys pass through.
// Nothing in the application imports this module.
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { z } from "zod";
import { EXPORTS_DIR } from "./exports";

const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "a YYYY-MM-DD date");
const month = z.string().regex(/^\d{4}-\d{2}$/, "a YYYY-MM month");

/** The planted handles the smoke suite reads: each names the row, account or vendor a feature of the household produced. */
const PlantedSchema = z.looseObject({
  /** the joint checking account: statement files, then the feed; its page is the one the suite opens */
  "seam.account": z.string(),
  "mortgage.account": z.string(),
  /** one of the mortgage's monthly payments (a row's uid): its vendor on its account is the mortgage's recurring item */
  "mortgage.payment": z.string(),
  "brokerage.account": z.string(),
  /** the account the feed stops reporting before the end (a balance-only one) */
  "stale.account": z.string(),
  /** a child's savings account: rows of its own, listed after the everyday accounts */
  "child.account": z.string(),
  "split.vendor": z.string(),
  /** a monthly subscription the household pays to the end */
  "subscriptions.monthly": z.string(),
  /** the vendor the family's answers teach */
  "learned.vendor": z.string(),
  /** the account holding the unmatched internal leg the data-gap list names */
  "data_gap.account": z.string(),
  /** the date of the row dated after the ingest day */
  "future.date": day,
});

/** the truth.json version this reader understands */
export const TRUTH_VERSION = 1;

export const TruthSchema = z.looseObject({
  version: z.literal(TRUTH_VERSION),
  profile: z.string(),
  end: day,
  household: z.looseObject({
    // the suites read the members only; the household's names pass through unchecked when present
    name: z.string().optional(),
    surname: z.string().optional(),
    joint_label: z.string().optional(),
    members: z.array(z.looseObject({ name: z.string(), role: z.enum(["adult", "child"]) })).min(1),
  }),
  accounts: z.record(z.string(), z.looseObject({
    kind: z.string(), name: z.string(), institution: z.string(), owner: z.string(),
  })),
  planted: PlantedSchema,
  figures: z.looseObject({
    /** the month ends that miss nothing the newest snapshot has, oldest first */
    comparable_month_ends: z.array(day),
    /** the month ends before every account reported */
    partial_month_ends: z.array(day),
    /** the $100,000 levels net worth passes across the comparable month ends, in cents */
    levels_crossed: z.array(z.number().int()),
    coverage_floor: month.nullable(),
    /** month -> its figures; the suite reads the months only */
    months: z.record(month, z.unknown()),
  }),
});

export type Truth = z.infer<typeof TruthSchema>;

/** Where the truth file is read from: FF_TRUTH, else truth.json two directories above the exports. */
export function truthPath(): string {
  const fromEnv = process.env.FF_TRUTH;
  if (fromEnv && !fromEnv.startsWith("/")) {
    throw new Error(`FF_TRUTH must be an absolute path (got a relative one: ${fromEnv})`);
  }
  return fromEnv || join(dirname(dirname(EXPORTS_DIR)), "truth.json");
}

/** The generator's truth for the household whose exports the suites read, validated; a missing handle or figure is an
 *  error that names it. */
export function loadTruth(): Truth {
  const path = truthPath();
  if (!existsSync(path)) throw new Error(`no truth file at ${path} (set FF_TRUTH to the generator's truth.json)`);
  const parsed = TruthSchema.safeParse(JSON.parse(readFileSync(path, "utf8")));
  if (!parsed.success) {
    const issues = parsed.error.issues.map((i) => `${i.path.join(".") || "(root)"}: ${i.message}`);
    throw new Error(`${path} is not the truth the suites need: ${issues.join("; ")}`);
  }
  return parsed.data;
}
