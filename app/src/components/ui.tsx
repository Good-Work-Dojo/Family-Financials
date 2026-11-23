// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import type { ReactNode } from "react";
import { answersRefusalText } from "../lib/answers";
import { clock } from "../lib/clock";
import { fmtDate } from "../lib/format";
import { useScrollHint } from "../lib/useScrollHint";

export function Card({ title, children, right, className = "", testId, id }: {
  title?: ReactNode; children: ReactNode; right?: ReactNode; className?: string; testId?: string;
  /** a real DOM id, for a card a link or another element lands on (lib/scrollTargets.ts; never the testid: that one is
   *  the test seam). Such a card keeps its top edge clear of the 48px sticky phone header when scrolled to, and its
   *  heading takes focus by script (tabIndex -1), so landing on it moves focus there. */
  id?: string;
}) {
  return (
    <div id={id} data-testid={testId}
      className={`rounded-xl border border-zinc-800 bg-zinc-900/60 p-4 ${id ? "scroll-mt-14 " : ""}${className}`}>
      {/* a card sits directly under its page's h1 (a hub's included), so its title is an h2; the class, not the tag,
          sets its size */}
      {(title || right) && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 tabIndex={id ? -1 : undefined}
            className={`min-w-0 text-sm font-semibold text-zinc-300${id ? " outline-none" : ""}`}>{title}</h2>
          {right && <div className="shrink-0">{right}</div>}
        </div>
      )}
      {children}
    </div>
  );
}

export function StatCard({ label, value, sub, tone, href, onClick, testId, tag }: {
  label: string; value: string; sub?: ReactNode; tone?: "pos" | "neg" | "warn";
  href?: string; onClick?: () => void; testId?: string;
  /** a small mark beside the value (components/DefaultTag.tsx: the figure rests on a default nobody stated) */
  tag?: ReactNode;
}) {
  const toneCls = tone === "pos" ? "text-emerald-400"
    : tone === "neg" ? "text-red-400"
    : tone === "warn" ? "text-amber-400" : "text-zinc-100";
  const interactive = href || onClick;
  const body = (
    <>
      <div className="text-xs uppercase tracking-wide text-zinc-500">{label}</div>
      <div className={`mt-1 text-xl font-semibold tabular-nums sm:text-2xl ${toneCls}`}>{value}{tag}</div>
      {sub && <div className="mt-1 text-xs text-zinc-400">{sub}</div>}
    </>
  );
  const cls = `block rounded-xl border border-zinc-800 bg-zinc-900/60 p-4 text-left ${
    interactive ? "transition-colors hover:border-zinc-600" : ""}`;
  if (href) return <a href={href} data-testid={testId} className={cls}>{body}</a>;
  if (onClick) return <button onClick={onClick} data-testid={testId} className={`${cls} w-full`}>{body}</button>;
  return <div data-testid={testId} className={cls}>{body}</div>;
}

/** Every balance renders one of these. Amber when stale (>45 days old). */
export function AsOfBadge({ date, assumed, generatedAt }: {
  date: string | null; assumed?: boolean; generatedAt?: string;
}) {
  if (!date) {
    return (
      <span className="inline-flex items-center rounded-full bg-amber-900/50 px-2 py-0.5 text-[10px] text-amber-300"
        title="No date in the source file — value may be stale">
        as of unknown
      </span>
    );
  }
  const ref = generatedAt ? new Date(generatedAt) : clock.now();
  const stale = (ref.getTime() - new Date(date).getTime()) / 86_400_000 > 45;
  return (
    <span
      className={`inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] ${
        stale ? "bg-amber-900/50 text-amber-300" : "bg-zinc-800 text-zinc-400"}`}
      title={(assumed ? "Date assumed from sibling exports. " : "")
        + (stale ? "Older than 45 days — consider refreshing this export." : "")}
    >
      as of {fmtDate(date)}{assumed ? "*" : ""}
    </span>
  );
}

/** A row of buttons picking one option: a group named by `label` (what the options choose between, for a screen reader),
 *  the selected button pressed (`aria-pressed`) */
export function SegmentToggle<T extends string>({ label, options, value, onChange }: {
  label: string; options: { value: T; label: string }[]; value: T; onChange: (v: T) => void;
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-lg border border-zinc-700 p-0.5 text-xs">
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={value === o.value} onClick={() => onChange(o.value)}
          className={`min-h-11 rounded-md px-2.5 py-1 md:min-h-0 ${value === o.value
            ? "bg-zinc-700 text-zinc-100" : "text-zinc-400 hover:text-zinc-200"}`}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** a colored status dot: the title for hover, the same words as screen-reader text */
export function StatusDot({ title, cls }: { title: string; cls: string }) {
  return (
    <span title={title} className={`inline-block h-2 w-2 rounded-full ${cls}`}>
      <span className="sr-only">{title}</span>
    </span>
  );
}

export function ReviewDot({ on, title = "needs review" }: { on: boolean; title?: string }) {
  if (!on) return null;
  return <StatusDot title={title} cls="bg-amber-400" />;
}

/** A control's hit area below the phone cut: at least 44 × 44 CSS px around its content, which stays centred at its own
 *  size (the glyph does not grow); nothing changes from md up. Two such targets side by side do not overlap: each is its
 *  own box. */
export const TAP = "max-md:inline-flex max-md:min-h-11 max-md:min-w-11 max-md:items-center max-md:justify-center";

/** A 44px-tall hit area below md for a control whose look keeps its own, smaller size (a pill, the freshness pill): a
 *  transparent box centred on it, as wide as it, that draws nothing and takes the taps. A row of such controls keeps
 *  8px between rows on a phone (`max-md:gap-y-2`) so no two hit areas overlap. */
export const TAP_AREA = "relative max-md:before:absolute max-md:before:inset-x-0 max-md:before:top-1/2 max-md:before:h-11 "
  + "max-md:before:-translate-y-1/2 max-md:before:content-['']";

/** A line of text that is one line, cut with an ellipsis, from md up, and wraps at a word below md, where an ellipsis
 *  would cut a name inside a word ("Joint Checkin…"). */
export const PHONE_WRAP = "truncate max-md:whitespace-normal max-md:break-words";

/** the card's own look as an OPAQUE fill (a sticky cell must cover what scrolls under it): the card is zinc-900 at 60%
 *  over the zinc-950 page, and both are theme variables, so this follows light mode too */
export const OPAQUE_CARD = "bg-[color-mix(in_srgb,var(--color-zinc-900)_60%,var(--color-zinc-950))]";
/** every row's first cell stuck to the scroller's left edge on the card's opaque fill: a table whose first column
 *  names its rows keeps them on screen while the figures pan */
const STICKY_FIRST = "[&_tr>:first-child]:sticky [&_tr>:first-child]:left-0 [&_tr>:first-child]:z-[1] "
  + "[&_tr>:first-child]:bg-[color-mix(in_srgb,var(--color-zinc-900)_60%,var(--color-zinc-950))]";

/** Wraps a wide table so it scrolls sideways within its container on small screens instead of overflowing the page;
 *  a no-op once the table fits. Works in any container (Card, bordered <details>, bare div) — no padding assumed.
 *  While the table is wider than its box the scroller says so: a "scroll →" cue at its top right while more lies to the
 *  right (gone at the end; its line is kept, so nothing moves), and it is a labelled region in the tab order, so a
 *  keyboard can scroll it with the arrow keys. `label` names that region (a screen reader hears it); `stickyFirst` keeps
 *  each row's first cell on screen while the rest pans (a table whose first column names its rows, on a card). */
export function TableScroll({ children, className = "", label, stickyFirst = false }: {
  children: ReactNode; className?: string; label: string; stickyFirst?: boolean;
}) {
  const { ref, overflows, more } = useScrollHint<HTMLDivElement>();
  return (
    <div ref={ref} role={overflows ? "region" : undefined} aria-label={overflows ? label : undefined}
      tabIndex={overflows ? 0 : undefined} data-testid="table-scroll"
      className={`overflow-x-auto ${overflows ? "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-blue-400" : ""} ${
        stickyFirst ? STICKY_FIRST : ""} ${className}`}>
      {overflows && (
        <div aria-hidden="true" data-testid="table-scroll-cue"
          className={`sticky left-0 h-4 text-right text-[10px] leading-4 text-faint ${more ? "" : "invisible"}`}>
          scroll →
        </div>
      )}
      {children}
    </div>
  );
}

/** `removeLabel` names the × button for assistive tech (a bare "×" reads as "multiplication x"); a
 *  caller that renders several chips says which one it removes */
export function Chip({ children, onRemove, removeLabel }: {
  children: ReactNode; onRemove?: () => void; removeLabel?: string;
}) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-zinc-800 px-2 py-0.5 text-xs text-zinc-300">
      {children}
      {onRemove && (
        <button type="button" onClick={onRemove} aria-label={removeLabel}
          className="text-zinc-500 hover:text-zinc-200">×</button>
      )}
    </span>
  );
}

/** Small status pill; `cls` carries the per-site color/extra classes. With a `title` the chip is
 *  focusable and repeats the tooltip as screen-reader text (a <span title> alone is mouse-only);
 *  `title` stays for hover. Pass `focusable={false}` when the chip sits inside something that is
 *  already interactive (a row button, a clickable row): the tooltip text is still read as part of
 *  that control, but the chip is not a second, nested tab stop. */
export function Badge({ cls, title, children, focusable = true }: {
  cls: string; title?: string; children: ReactNode; focusable?: boolean;
}) {
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] ${cls}`} title={title}
      tabIndex={title && focusable ? 0 : undefined}>
      {children}
      {title && <span className="sr-only">: {title}</span>}
    </span>
  );
}

/** Amber note shown when edits stay local: the family store is unreachable, or it `refused` the last save (the
 *  status a store API reports: 413 = too large, e.g. a prefs doc over the cap once the store adds back its lists). */
export function LocalOnlyNotice({ what, refused }: { what?: string; refused?: number | null }) {
  const why = refused ? `The family store refused the last save${refused === 413 ? " as too large" : ""}` : "Store unreachable";
  return (
    <span className="ml-1 text-amber-400">
      {`${why} — ${what ? `${what} are ` : ""}saved in this browser only for now.`}
    </span>
  );
}

/** LocalOnlyNotice for the review answers: an unreachable store is retried by itself, so that one stays "for now";
 *  a save the store refused is not, and says what to do instead (answersRefusalText). */
export function AnswersLocalOnlyNotice({ refused }: { refused: number | null }) {
  return refused
    ? <span className="ml-1 text-amber-400">{answersRefusalText(refused)}</span>
    : <LocalOnlyNotice what="answers" />;
}

export function EditButton({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button onClick={onClick}
      className="rounded-md border border-zinc-700 px-2 py-1 text-[11px] text-zinc-300 hover:bg-zinc-800">
      {children}
    </button>
  );
}

export function DoneButton({ onClick }: { onClick: () => void }) {
  return (
    <button onClick={onClick}
      className="rounded-md bg-emerald-600 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-emerald-500">
      Done
    </button>
  );
}

/** Toggleable filter/choice pill's look; callers supply the active/inactive colors. It keeps its own size: a pill that is
 *  a button takes TAP_AREA for its 44px phone hit area, and one drawn inside a 44px button (Recurring's) needs nothing. */
export function pillCls(active: boolean, activeCls: string, inactiveCls: string): string {
  return `min-h-[36px] rounded-full border px-3 py-1.5 text-sm sm:min-h-0 sm:text-xs ${
    active ? activeCls : inactiveCls}`;
}

/** the key of a list line (Line): at least 4.5:1 on a card in both themes (lib/contrast.test.ts) */
export const LINE_KEY = "text-zinc-400";

/** one label / value row of a key-fact list (a <ul> of these) */
export function Line({ k, v }: { k: string; v: string }) {
  return (
    <li className="flex justify-between gap-2">
      <span className={LINE_KEY}>{k}</span>
      <span className="text-right tabular-nums text-zinc-200">{v}</span>
    </li>
  );
}
