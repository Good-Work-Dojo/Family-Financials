// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Net-worth milestones: where the snapshot series first crosses each $100K level. Home's net-worth chart pins the
// newest of them.
import type { AppData } from "../data/types";
import { localISO } from "./format";

export interface MilestoneCrossing { level: number; date: string; daggered: boolean }

/** first (interpolated) crossing of each $100K level across the snapshot series */
export function milestoneCrossings(data: AppData): MilestoneCrossing[] {
  const snaps = data.assets.snapshots;
  const out: MilestoneCrossing[] = [];
  const seen = new Set<number>();
  for (let i = 1; i < snaps.length; i++) {
    const a = snaps[i - 1], b = snaps[i];
    if (b.total === a.total) continue;   // a flat step crosses nothing (and its share of the step would be 0/0)
    const lo = Math.min(a.total, b.total), hi = Math.max(a.total, b.total);
    for (let level = Math.ceil(lo / 100_000) * 100_000; level <= hi; level += 100_000) {
      if (seen.has(level) || b.total < a.total) continue;   // only upward firsts
      seen.add(level);
      const f = (level - a.total) / (b.total - a.total);
      const ta = new Date(a.snapshot_date + "T00:00:00").getTime();
      const tb = new Date(b.snapshot_date + "T00:00:00").getTime();
      const d = new Date(ta + f * (tb - ta));
      out.push({
        level,
        date: localISO(d),
        daggered: a.mortgage_included !== b.mortgage_included,
      });
    }
  }
  return out;
}
