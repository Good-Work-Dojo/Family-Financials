// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useMemo } from "react";
import type { BalancesTimeseries } from "../data/types";
import { chartSpacing } from "../lib/balanceCharts";
import { fmtDate } from "../lib/format";

/** The line under a balance chart whose histories turn from one balance a month to frequent ones (lib/balanceCharts.ts
 *  chartSpacing), so the reader knows why a line moves more after a date (a month's swings show only where the points
 *  are frequent). True of every line on the chart: one date when every line turns on it, else "some accounts" and the
 *  latest date. Nothing when no line turns. */
export default function BalanceSpacingNote({ series, ids }: {
  series: BalancesTimeseries["series"];
  ids: readonly string[];
}) {
  const spacing = useMemo(() => chartSpacing(series, ids), [series, ids]);
  return spacing === null ? null : (
    <p data-testid="balance-spacing" className="mt-1 text-[10px] text-faint">
      {spacing.every
        ? `One balance a month through ${fmtDate(spacing.date)}; more often since.`
        : `Some accounts have one balance a month in their early history, the latest through ${fmtDate(spacing.date)}; `
          + "more often since."}
    </p>
  );
}
