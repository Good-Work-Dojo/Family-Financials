// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import echarts, { legendRows, legendRowStep, stillOption, tuneForMobile, withLegendRows } from "../lib/echarts";
import type { EChartsType } from "echarts/core";
import type { ChartTable } from "../lib/chartTable";
import { useIsMobile, useReducedMotion } from "../lib/useMediaQuery";
import { usePrivacy } from "../lib/usePrivacy";
import ChartDataTable, { CsvButton, FOOTER_BTN } from "./ChartDataTable";

interface Props {
  option: object;
  height?: number;
  onClick?: (params: any) => void;
  className?: string;
  /** the data behind the chart: adds a "view data" toggle under it (a real table + a CSV download).
   *  Build it in the memo that builds `option`, from the same arrays (lib/chartTable.ts). */
  tableData?: ChartTable;
  /** the view-data footer's CSV is not offered (its table is about to change: Reports' deferred spec) */
  csvDisabled?: boolean;
  /** the height on a phone, in place of the 240px cap: a chart whose labels or tap targets need the room (the
   *  money-flow graph, the phone's calendar) */
  phoneHeight?: number;
}

/** The one chart wrapper every visualization flows through. */
export default function EChart({ option, height = 320, onClick, className, tableData, csvDisabled, phoneHeight }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<EChartsType | null>(null);
  const clickRef = useRef(onClick);
  clickRef.current = onClick;
  // privacy mode blurs the CANVAS (not the box: ECharts appends its tooltip DOM inside the box, and the tooltips are
  // already masked, so a percentage-only chart keeps a readable tooltip) on top of the masked axes and labels; the
  // footer's controls stay sharp
  const [privacy] = usePrivacy();
  // shorter charts on phones so two don't fill the whole screen; the fit below re-sizes the chart
  // when this height changes
  const isMobile = useIsMobile();
  // a viewer who asked for less motion gets charts that draw in their final state (and the screenshot
  // driver emulates it, so no PNG catches a line half-grown)
  const reducedMotion = useReducedMotion();
  // the box's width, for a wrapping legend (lib/echarts.ts wrapLegend, and every legend on a phone): the rows past the
  // first it takes at this width move the plot down and make the chart that much taller, so the plot keeps its size; 0
  // until the box is laid out
  const [boxWidth, setBoxWidth] = useState(0);
  const shaped = useMemo(() => (isMobile ? tuneForMobile(option) : option), [option, isMobile]);
  const extraRows = useMemo(() => (legendRows(shaped, boxWidth) ?? 1) - 1, [shaped, boxWidth]);
  const step = legendRowStep(shaped);
  // a phone caps a chart at 240px unless its author keeps the height it asked for (`phoneHeight`)
  const baseHeight = isMobile ? phoneHeight ?? Math.min(height, 240) : height;
  const h = baseHeight + extraRows * step;
  // the width a pie under a legend of several rows is sized from; a chart with no extra row does not depend on it, so a
  // first layout's width does not hand it a new option
  const sizedWidth = extraRows > 0 ? boxWidth : 0;
  const tuned = useMemo(() => {
    const t = withLegendRows(shaped, extraRows, step, { width: sizedWidth, height: baseHeight });
    return reducedMotion ? stillOption(t) : t;
  }, [shaped, reducedMotion, extraRows, step, sizedWidth, baseHeight]);
  const tunedRef = useRef(tuned);
  tunedRef.current = tuned;
  // the option object the chart last received, so the mount-time init and the option effect never
  // both apply the same one
  const applied = useRef<object | null>(null);
  const fitRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    const el = ref.current!;
    let chart: EChartsType | null = null;
    // Fit the chart to its box: on mount, on every observed resize of the box, and on every window
    // resize. A box with no size (a display:none ancestor, not laid out yet) is skipped - the chart
    // is created only once its box has a real size, and a laid-out chart keeps its last good layout
    // instead of collapsing to 0. Each fit compares the chart's own size with the box's rather than
    // trusting that a notification arrived: a chart laid out at a stale (e.g. near-zero first-frame)
    // width is re-fitted at the next chance, whichever event delivers it - a 1280px screenshot once
    // caught Accounts -> Liabilities' charts frozen as ~30px slivers.
    const fit = () => {
      const w = el.clientWidth, ht = el.clientHeight;
      if (!w || !ht) return;
      setBoxWidth(w);
      if (!chart) {
        chart = echarts.init(el);
        chartRef.current = chart;
        chart.on("click", (p) => clickRef.current?.(p));
        applied.current = tunedRef.current;
        chart.setOption(tunedRef.current as any, { notMerge: true });
        return;
      }
      if (chart.getWidth() !== w || chart.getHeight() !== ht) chart.resize();
    };
    fitRef.current = fit;
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    window.addEventListener("resize", fit);
    fit();
    return () => {
      fitRef.current = null;
      ro.disconnect();
      window.removeEventListener("resize", fit);
      chart?.dispose();
      chartRef.current = null;
      applied.current = null;
    };
  }, []);

  // React itself knows when the layout flips across md (the height changes, so may the width):
  // re-fit synchronously at that commit, independent of any observer or resize event
  useLayoutEffect(() => { fitRef.current?.(); }, [isMobile, h]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || applied.current === tuned) return;
    applied.current = tuned;
    chart.setOption(tuned as any, { notMerge: true });
  }, [tuned]);

  return (
    <>
      <div ref={ref} style={{ height: h }} className={`${className ?? ""} ${privacy ? "[&_canvas]:blur-[6px]" : ""}`.trim() || undefined} />
      {tableData && <ChartDataFooter table={tableData} csvDisabled={csvDisabled} />}
    </>
  );
}

/** "view data": the chart's own numbers as a table (components/ChartDataTable.tsx: shown through the table's
 *  formatter, which privacy mode's mask rides on) and a CSV of the raw rows. A gap is a dash, never a 0. The
 *  toggle's visible text stays "view data" (aria-expanded says open or closed); `table.label` makes its
 *  accessible name distinct per chart. */
function ChartDataFooter({ table, csvDisabled }: { table: ChartTable; csvDisabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  return (
    <div className="mt-1 text-[10px]">
      <div className="flex items-center gap-3">
        <button type="button" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen(!open)}
          aria-label={table.label ? `View data: ${table.label}` : undefined}
          className={FOOTER_BTN}>view data</button>
        {open && <CsvButton table={table} disabled={csvDisabled} />}
      </div>
      <div id={panelId}>{open && <ChartDataTable table={table} />}</div>
    </div>
  );
}
