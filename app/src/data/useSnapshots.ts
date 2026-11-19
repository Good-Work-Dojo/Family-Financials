// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useMemo } from "react";
import { useData } from "./DataContext";
import type { Snapshot } from "./types";

/** The net-worth snapshots a page compares: `all` is the loaded list (`data.assets.snapshots`: the month ends comparable
 *  with the newest, oldest first, then the current snapshot), `latest` its last element and `prior` the one before it.
 *  Each is null when the list is too short to hold it: a household with no snapshot has no latest, one with a single
 *  snapshot has no prior, so nothing is ever compared with itself. */
export interface Snapshots {
  all: Snapshot[];
  latest: Snapshot | null;
  prior: Snapshot | null;
}

/** the accessors of a snapshot list, for library code and tests that hold one without React */
export function snapshotsOf(list: Snapshot[]): Snapshots {
  return { all: list, latest: list[list.length - 1] ?? null, prior: list[list.length - 2] ?? null };
}

/** the loaded snapshots through their accessors; one object per list, so it is a stable memo dependency */
export function useSnapshots(): Snapshots {
  const list = useData().data.assets.snapshots;
  return useMemo(() => snapshotsOf(list), [list]);
}
