// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
/** Loading placeholders for Suspense fallbacks and the initial data load. Every root
 *  carries aria-busy, which tools/verify/screenshots_driver.py waits on before shooting. */
export default function Skeleton({ variant = "card", lines = 3 }: {
  variant?: "page" | "card" | "table" | "chart"; lines?: number;
}) {
  const box = "animate-pulse rounded-xl border border-zinc-800 bg-zinc-900/60 p-4";
  if (variant === "page") {
    return (
      <div role="status" aria-busy="true" aria-label="Loading" className="animate-pulse space-y-4">
        <div className="h-6 w-48 rounded bg-zinc-800" />
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[0, 1, 2, 3].map((i) => <div key={i} className="h-16 rounded-xl bg-zinc-900" />)}
        </div>
        <div className={`${box} h-64`} />
        <div className={box}>
          {[0, 1, 2].map((i) => (
            <div key={i} className="mb-2 h-3 rounded bg-zinc-800" style={{ width: `${90 - i * 12}%` }} />
          ))}
        </div>
      </div>
    );
  }
  if (variant === "chart") {
    return <div role="status" aria-busy="true" aria-label="Loading chart" className={`${box} h-64`} />;
  }
  return (
    <div role="status" aria-busy="true" aria-label={variant === "table" ? "Loading table" : "Loading"}
      className={box}>
      {variant === "card" && <div className="mb-3 h-3 w-1/3 rounded bg-zinc-800" />}
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="mb-2 h-3 rounded bg-zinc-800/70"
          style={{ width: variant === "table" ? `${90 - i * 12}%` : "100%" }} />
      ))}
    </div>
  );
}
