// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
import { type ReactNode, useEffect, useId, useMemo, useRef, useState } from "react";
import { useRefreshFollow } from "../../components/DataRefresh";
import { CurrencyInput } from "../../components/inputs";
import { Code, EmptyState } from "../../components/NoData";
import { MissingDataCard } from "../../components/RequireData";
import Skeleton from "../../components/Skeleton";
import { Card } from "../../components/ui";
import { useData } from "../../data/DataContext";
import type { AssetClasses, ManualAsset } from "../../data/types";
import { useIngestFollow } from "../../data/useIngestFollow";
import { useManualAssetsDraft } from "../../data/useManualAssetsDraft";
import { useManualAssetsStore } from "../../data/useManualAssetsStore";
import { useSnapshots } from "../../data/useSnapshots";
import { clock } from "../../lib/clock";
import { IS_DEMO } from "../../lib/demo";
import { countOf, fmtDate, fmtUSD0 } from "../../lib/format";
import { type Follow, ingestFailure } from "../../lib/ingestFollow";
import {
  acquiredQuestion, addValue, afterNewestReport, type AssetValue, cardRows, type CardRow, changedEntries, classColor,
  classOptions, compareSaved, type ConfigView, copyOf, countedAsSaved, type DraftContext, forSave, hasChanges, isDebtClass, isFirstValue,
  isLive, type LiveAsset, loadProblem, type ManualAssetsDoc, MAX_ASSETS, newAssetId, type Problem, rebaseDraft, refusalText,
  removeAsset, removedElsewhere, removeValue, signedFor, skippedById, upsertAsset, validateDraft,
} from "../../lib/manualAssets";
import { SCROLL_TARGETS } from "../../lib/scrollTargets";

// Settings → Manual assets: the editor for what no account reports — a home, a vehicle, a debt entered by hand — that
// net worth counts. The list is the exports' (accounts.json `manual_assets`, what the last ingest counted, and
// `manual_overridden`, the config entries an app asset hides) with this session's unsaved draft over it
// (lib/manualAssets.ts cardRows; the draft outlives a visit to another page, data/useManualAssetsDraft.tsx). An asset
// entered in the app is edited in place; a config entry is read-only ("from your config"), and Edit a copy makes an app
// asset of its id that overrides it, which "Use the config's value again" removes. "Saved" means what the family store
// acknowledged: an edit it refused, or one not sent yet, stays unsaved. Nothing is sent until Save (Revert drops the
// draft); the store merges the save by asset id and asks for an ingest without a fetch, which the card follows
// (followStep) until the reloaded exports hold what was saved, the ingest fails, or the follow's bound passes. Every
// amount goes through the masked formatters and the masked input (privacy mode hides them; an amount box shows its
// figure only while focused).

/** where an asset was entered, in words */
const ORIGIN = {
  config: "from your config",
  app: "entered in this dashboard",
  copy: "your copy of the entry in your config",
} as const;

export default function ManualAssetsCard() {
  const { missing } = useData();
  return (
    <Card id={SCROLL_TARGETS.manualAssets} testId="card-manual-assets" title="Manual assets">
      <p className="text-sm text-zinc-300">
        A hand-entered asset is something the household owns that no account reports — a home, a vehicle, anything else
        — at a value you state, which net worth counts. A debt no account reports is entered the same way, under a debt
        class.
      </p>
      <div className="mt-3">
        {missing.has("accounts.json")
          ? <MissingDataCard compact files={["accounts.json"]} what="The hand-entered assets" />
          : <Editor />}
      </div>
    </Card>
  );
}

/** the latest value of a list (the newest day with an amount), or null */
const latestOf = (values: readonly AssetValue[]): AssetValue | null =>
  values.filter((v) => v.value !== null).reduce<AssetValue | null>((best, v) => (best === null || v.as_of > best.as_of ? v : best), null);

/** a config entry as the words that name the value it brings back: its name and latest value */
const entryValue = (e: ManualAsset): string => {
  const last = latestOf(e.values);
  return last ? `${e.name}, ${fmtUSD0(last.value!)}` : e.name;
};

/** what the card says about a save, by where it stands; null says nothing */
function followLine(f: Follow, localOnly: boolean, upToDate: boolean): ReactNode {
  switch (f.phase) {
    case "idle": case "refused": return null;
    case "saving":
      return localOnly
        ? "Not saved yet: the family store is unreachable. The change stays in this browser and is sent once the store answers."
        : "Saving…";
    case "asking": case "waiting": case "reloading":
      return "Saved — the dashboard updates after the next ingest.";
    case "done":
      return upToDate ? "Up to date: the dashboard counts what was saved."
        : "The ingest ran, but the dashboard does not count everything saved yet: see the notes below.";
    case "failed":
      return `Saved, but the last ingest ${ingestFailure(f.rc ?? 0)}. The dashboard still shows what it counted before.`;
    case "later": return "Saved. The dashboard shows it after the next scheduled refresh or ingest.";
    // the demo build keeps the save in this browser and is never rebuilt
    case "no-service": return IS_DEMO
      ? "Saved in this browser. The demo is never rebuilt, so net worth does not count it."
      : "Saved. Re-run the ingest to see it.";
  }
}

/** a problem's identity, for telling one the stored copy already had from one the draft makes */
const problemKey = (p: Problem) => `${p.id}|${p.field}|${p.index ?? ""}|${p.message}`;

function Editor() {
  const { data, indexes } = useData();
  const store = useManualAssetsStore();
  const refresh = useRefreshFollow();
  const { session, setSession } = useManualAssetsDraft();
  const latest = useSnapshots().latest;
  const classes = data.assumptions.asset_classes;
  const zone = data.assumptions.household.timezone;
  const generatedAt = data.manifest.generated_at;
  const view = useMemo((): ConfigView => ({ exported: data.manualAssets, overridden: data.manualOverridden }),
    [data.manualAssets, data.manualOverridden]);
  // net worth dates nothing past the accounts' newest report (the newest snapshot, when an account has a row in it)
  const newestReport = latest !== null && latest.rows.some((r) => r.manual_id === null) ? latest.snapshot_date : null;
  const dataFirst = indexes.dataRange?.first ?? null;
  // what "saved" means: the copy the family store acknowledged (before its first answer, this browser's own)
  const stored = store.acked ?? store.doc;

  // The session's draft, carried over the stored copy when the card mounts and whenever that copy moves (an echo,
  // another device's save): an entry the draft did not touch takes the newer copy. The first draft is this browser's
  // own copy, so an edit the store refused or has not received yet shows as unsaved.
  useEffect(() => {
    if (store.loading) return;
    setSession((s) => {
      if (s === null) return { draft: store.doc, base: stored, open: [], removedElsewhere: [] };
      if (s.base === stored) return s;
      const gone = removedElsewhere(s.base, s.draft, stored);
      return { ...s, draft: rebaseDraft(s.base, s.draft, stored), base: stored,
               removedElsewhere: [...new Set([...s.removedElsewhere, ...gone])] };
    });
  }, [store.loading, store.doc, stored, setSession]);
  const draft = session?.draft ?? stored;
  const open = useMemo(() => new Set(session?.open ?? []), [session?.open]);
  const setDraft = (f: (d: ManualAssetsDoc) => ManualAssetsDoc) =>
    setSession((s) => (s === null ? s : { ...s, draft: f(s.draft) }));
  const toggle = (id: string, on?: boolean) => setSession((s) => {
    if (s === null) return s;
    const next = new Set(s.open);
    if (on ?? !next.has(id)) next.add(id); else next.delete(id);
    return { ...s, open: [...next] };
  });

  const ctx = useMemo((): DraftContext => ({
    owners: data.assumptions.owners, classes, accountNames: data.accounts.map((a) => a.name),
    configEntries: [...view.exported.filter((e) => e.origin === "config"), ...view.overridden],
  }), [data.assumptions.owners, classes, data.accounts, view]);
  const changed = useMemo(() => new Set(changedEntries(stored, draft).map((a) => a.id)), [stored, draft]);
  const dirty = changed.size > 0;
  // A problem the stored copy already had on an asset the draft leaves alone is that row's warning, not a block on
  // every save; a problem the draft makes (on a changed asset, or one a change elsewhere causes) blocks Save.
  const { blocking, problems } = useMemo(() => {
    const all = validateDraft(draft, ctx);
    const before = new Set(validateDraft(stored, ctx).map(problemKey));
    return { problems: all,
             blocking: all.filter((p) => p.id === null || changed.has(p.id) || !before.has(problemKey(p))) };
  }, [draft, stored, ctx, changed]);
  const rows = useMemo(() => cardRows(view, draft, stored), [view, draft, stored]);
  const skipped = useMemo(() => skippedById(data.quality.checks), [data.quality.checks]);
  // Save pressed while a problem stops it: every open field shows its error from then on (until a save or a revert)
  const [saveTried, setSaveTried] = useState(false);
  const unreadable = loadProblem(data.quality.checks) !== null;

  // the after-save loop: the store's answer, then the ingest it asked for, then the reloaded exports (the shared follow,
  // data/useIngestFollow.ts: its bound, its one more look and its grace)
  const { follow, dispatch, landed } = useIngestFollow(refresh, generatedAt, "save");
  const counts = useRef({ accepted: 0, refusals: 0 });
  const landedRef = useRef(landed);
  landedRef.current = landed;
  useEffect(() => {
    if (follow.phase !== "saving" || store.accepted <= counts.current.accepted) return;
    landedRef.current();
  }, [follow.phase, store.accepted]);
  useEffect(() => {
    if (follow.phase === "saving" && store.refusals > counts.current.refusals) dispatch({ type: "refused" });
  }, [follow.phase, store.refusals, dispatch]);
  const upToDate = useMemo(() => follow.phase === "done"
    && compareSaved(stored, follow.removed, view.exported, data.quality.checks).upToDate,
  [follow.phase, follow.removed, stored, view.exported, data.quality.checks]);

  const saveId = useId(), summaryId = useId();
  const revertRef = useRef<HTMLButtonElement>(null);
  const addRef = useRef<HTMLButtonElement>(null);
  const focusAfter = useRef<string | null>(null);
  useEffect(() => {
    const id = focusAfter.current;
    if (id === null) return;
    focusAfter.current = null;
    document.getElementById(id)?.focus();
  });

  if (store.loading || session === null) return <Skeleton variant="card" lines={2} />;
  // the store has not answered since the page loaded: this browser holds no copy of the family's list to edit
  if (store.localOnly && store.refused === null && follow.phase === "idle") {
    return <ReadOnlyList exported={view.exported} skipped={skipped} />;
  }

  const now = () => clock.nowISO();
  const edit = (a: LiveAsset) => setDraft((d) => upsertAsset(d, a, now()));
  const add = () => {
    const id = newAssetId(draft.assets.map((a) => a.id));
    setDraft((d) => upsertAsset(d, { id, name: "", owner: "", asset_class: "", liquid: false, acquired: null,
      values: [{ as_of: clock.today(zone), value: null }], updated_at: now() }, now()));
    toggle(id, true);
    focusAfter.current = `${saveId}-name-${id}`;
  };
  const copy = (e: ManualAsset) => {
    setDraft((d) => upsertAsset(d, copyOf(e, now()), now()));
    toggle(e.id, true);
    focusAfter.current = `${saveId}-name-${e.id}`;
  };
  // removing an asset takes its buttons away: focus goes to Revert, which can bring it back
  const remove = (id: string) => {
    setDraft((d) => removeAsset(d, id, now()));
    toggle(id, false);
    revertRef.current?.focus();
  };
  const save = () => {
    if (!dirty || blocking.length > 0) {
      if (dirty) setSaveTried(true);
      return;
    }
    setSaveTried(false);
    const removed = changedEntries(stored, draft).filter((a) => !isLive(a)).map((a) => a.id);
    const next = forSave(stored, draft, now());
    counts.current = { accepted: store.accepted, refusals: store.refusals };
    dispatch({ type: "save", removed });
    // the draft is what was sent; it is saved once the store acknowledges it (the draft is then carried over its echo)
    setSession({ draft: next, base: stored, open: [], removedElsewhere: [] });
    store.save(next);
  };
  const revert = () => {
    if (!dirty) return;
    setSaveTried(false);
    setSession({ draft: stored, base: stored, open: [], removedElsewhere: [] });
    // this browser's copy holds an edit the store refused or has not received: send the store's own copy back, so the
    // edit is not sent again on the next load
    if (hasChanges(stored, store.doc)) store.save(stored);
    dispatch({ type: "reset" });
    addRef.current?.focus();
  };

  const live = draft.assets.filter(isLive).length;
  const blocked = !dirty || blocking.length > 0;
  const nameOf = (id: string | null) => {
    const a = id === null ? null : draft.assets.find((x) => x.id === id);
    return a ? a.name.trim() || "The new asset" : "The list";
  };
  const refusal = store.refused !== null ? refusalText(store.refused, store.refusedReason) : null;
  const line = refusal ?? followLine(follow, store.localOnly, upToDate);
  const bad = refusal !== null || follow.phase === "failed";
  return (
    <>
      {unreadable && (
        <p className="mb-2 text-xs text-amber-400">
          The last ingest could not read the assets saved here, so it left them out; the config's entries still count.
        </p>
      )}
      <p role="status" data-testid="manual-assets-status" className={`text-sm ${bad ? "text-amber-400"
        : follow.phase === "done" && upToDate ? "text-emerald-400" : "text-zinc-300"}`}>
        {line}
      </p>
      {rows.length === 0 ? (
        <EmptyState>
          None yet. Add one here, or state one in the household's config
          (<Code>manual_entries:</Code> in <Code>accounts.yaml</Code>).
        </EmptyState>
      ) : (
        <>
          {/* the saved ones: a config entry, or an app asset the store holds; a draft not saved yet is not one */}
          <p data-testid="manual-assets-count" className="text-xs text-zinc-400">
            {countOf(rows.filter((r) => r.kind !== "removal"
              && (r.kind !== "app" || stored.assets.some((a) => a.id === r.id && isLive(a)))).length, "hand-entered asset")}
          </p>
          <ul className="mt-1 divide-y divide-zinc-800 border-y border-zinc-800">
            {rows.map((r) => (
              <li key={r.id} data-testid={`manual-asset-${r.id}`} className="py-3">
                {r.kind === "app" ? (
                  <AppAsset row={r} open={open.has(r.id)} onToggle={() => toggle(r.id)} onChange={edit}
                    onRemove={() => remove(r.id)} classes={classes} owners={data.assumptions.owners}
                    problems={problems.filter((p) => p.id === r.id)} unsaved={changed.has(r.id)}
                    blocking={blocking.some((p) => p.id === r.id)}
                    stored={stored.assets.find((a) => a.id === r.id)} skippedWhy={skipped.get(r.id)}
                    removedElsewhere={session.removedElsewhere.includes(r.id)}
                    newestReport={newestReport} dataFirst={dataFirst} today={clock.today(zone)}
                    idPrefix={saveId} onAddValue={(v) => setDraft((d) => addValue(d, r.id, v, now()))}
                    onRemoveValue={(i) => setDraft((d) => removeValue(d, r.id, i, now()))} showAll={saveTried} />
                ) : (
                  <OtherAsset row={r} onCopy={copy} skippedWhy={skipped.get(r.id)} />
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button ref={addRef} type="button" onClick={add} disabled={live >= MAX_ASSETS} data-testid="manual-assets-add"
          className="min-h-11 min-w-11 rounded-md border border-zinc-700 px-3 text-sm text-zinc-200 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-60 md:min-h-0 md:min-w-0 md:py-1">
          Add an asset
        </button>
        {live >= MAX_ASSETS && <span className="text-xs text-zinc-400">At most {MAX_ASSETS} assets: remove one to add another.</span>}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-zinc-800 pt-3">
        {/* aria-disabled, not disabled: the buttons stay in the Tab order, so their description (what is unsaved,
            what is still to fix) is heard; a click does nothing then */}
        <button id={saveId} type="button" onClick={save} aria-disabled={blocked || undefined}
          aria-describedby={summaryId}
          className={`min-h-11 min-w-11 rounded-md px-3 text-sm font-medium md:min-h-0 md:min-w-0 md:py-1 ${blocked
            ? "cursor-not-allowed bg-zinc-800 text-zinc-500" : "bg-emerald-600 text-white hover:bg-emerald-500"}`}>
          Save
        </button>
        <button ref={revertRef} type="button" onClick={revert} aria-disabled={!dirty || undefined}
          aria-describedby={summaryId}
          className={`min-h-11 min-w-11 rounded-md border border-zinc-700 px-3 text-sm md:min-h-0 md:min-w-0 md:py-1 ${!dirty
            ? "cursor-not-allowed text-zinc-500" : "text-zinc-200 hover:bg-zinc-800"}`}>
          Revert
        </button>
        <div id={summaryId} data-testid="manual-assets-summary" className="min-w-0 text-xs text-zinc-400">
          {!dirty ? "No unsaved changes." : blocking.length === 0 ? "Unsaved changes." : (
            <>
              Unsaved changes · {countOf(blocking.length, "problem")} to fix before saving:
              <ul className="mt-1 list-disc pl-4 text-red-400">
                {blocking.map((p, i) => <li key={i}>{nameOf(p.id)}: {p.message}</li>)}
              </ul>
            </>
          )}
        </div>
      </div>
    </>
  );
}

/** the list the last ingest counted, read-only, while the family store cannot be reached */
function ReadOnlyList({ exported, skipped }: { exported: readonly ManualAsset[]; skipped: Map<string, string> }) {
  return (
    <>
      <p className="text-xs text-amber-400">
        The family store is unreachable, so the assets cannot be changed right now. This is what the last ingest counted.
      </p>
      {exported.length === 0 ? <EmptyState>None yet.</EmptyState> : (
        <>
          <p data-testid="manual-assets-count" className="mt-2 text-xs text-zinc-400">{countOf(exported.length, "hand-entered asset")}</p>
          <ul className="mt-1 divide-y divide-zinc-800 border-y border-zinc-800">
            {exported.map((e) => (
              <li key={e.id} data-testid={`manual-asset-${e.id}`} className="py-3">
                <Summary name={e.name} cls={e.asset_class} owner={e.owner} values={e.values} origin={ORIGIN[e.origin]} />
                {e.origin === "app" && skipped.has(e.id) && <SkipNote why={skipped.get(e.id)!} />}
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

/** one asset's name, class, owner, where it was entered and its latest value (on its own line below `sm`, so every
 *  row's amount sits in the same place on a phone) */
function Summary({ name, cls, owner, values, origin, tag }: {
  name: string; cls: string; owner: string; values: readonly AssetValue[]; origin: string; tag?: string;
}) {
  const last = latestOf(values);
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
      <div className="min-w-0">
        <p className="break-words text-sm font-medium text-zinc-200">
          {name.trim() || "New asset"}
          {tag && <span className="ml-2 rounded bg-amber-900/40 px-1.5 py-0.5 text-[10px] font-normal text-amber-300">{tag}</span>}
        </p>
        <p className="text-xs text-zinc-400">
          {cls && <span aria-hidden="true" className="mr-1 inline-block size-2 rounded-full align-middle" style={{ background: classColor(cls) }} />}
          {[cls || "no class yet", owner || "no owner yet", origin].join(" · ")}
        </p>
      </div>
      {last && (
        <p className="w-full text-sm tabular-nums text-zinc-200 sm:w-auto sm:shrink-0 sm:text-right">
          {fmtUSD0(last.value!)} <span className="text-xs text-zinc-500">as of {fmtDate(last.as_of)}</span>
        </p>
      )}
    </div>
  );
}

const SkipNote = ({ why }: { why: string }) => (
  <p className="mt-1 text-xs text-amber-400">Left out of net worth by the last ingest: {why}.</p>
);

/** a config entry, a removal not ingested yet, or an asset the store no longer holds: read-only */
function OtherAsset({ row, onCopy, skippedWhy }: {
  row: Exclude<CardRow, { kind: "app" }>; onCopy: (e: ManualAsset) => void; skippedWhy: string | undefined;
}) {
  if (row.kind === "removal") {
    const words = row.entry !== null
      ? `${row.saved ? "The" : "Once saved, the"} config's value (${entryValue(row.entry)}) counts again after the next ingest.`
      : row.saved ? "Removed — it leaves the dashboard after the next ingest."
        : "Removed — once saved, it leaves the dashboard after the next ingest.";
    return (
      <>
        <p className="text-sm font-medium text-zinc-400 line-through decoration-zinc-600">{row.name}</p>
        <p data-testid="manual-asset-removal" className="text-xs text-zinc-400">{words}</p>
      </>
    );
  }
  const e = row.entry;
  if (row.kind === "gone") {
    return (
      <>
        <Summary name={e.name} cls={e.asset_class} owner={e.owner} values={e.values} origin={ORIGIN.app} />
        <p className="mt-1 text-xs text-amber-400">
          Counted at the last ingest, but the family store no longer holds it, so the next ingest leaves it out.
        </p>
        {skippedWhy && <SkipNote why={skippedWhy} />}
      </>
    );
  }
  return (
    <>
      <Summary name={e.name} cls={e.asset_class} owner={e.owner} values={e.values} origin={ORIGIN.config} />
      {e.acquired && <p className="text-xs text-zinc-500">Acquired {fmtDate(e.acquired)}.</p>}
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
        <button type="button" onClick={() => onCopy(e)} aria-label={`Edit a copy of ${e.name}`}
          className="inline-flex min-h-11 min-w-11 items-center text-xs text-blue-400 hover:underline md:min-h-0 md:min-w-0">
          Edit a copy
        </button>
        <span className="text-xs text-zinc-500">
          To delete it, edit the household's config (<Code>manual_entries:</Code> in <Code>accounts.yaml</Code>).
        </span>
      </div>
    </>
  );
}

/** an asset entered in the app: its summary and notes, Edit and Remove (or, for a copy, the config's value again),
 *  and while open the fields */
function AppAsset({ row, open, onToggle, onChange, onRemove, classes, owners, problems, unsaved, blocking, stored,
  skippedWhy, removedElsewhere, newestReport, dataFirst, today, idPrefix, onAddValue, onRemoveValue, showAll }: {
  row: Extract<CardRow, { kind: "app" }>; open: boolean; onToggle: () => void; onChange: (a: LiveAsset) => void;
  onRemove: () => void; classes: AssetClasses; owners: readonly string[]; problems: Problem[]; unsaved: boolean;
  blocking: boolean; stored: ManualAssetsDoc["assets"][number] | undefined; skippedWhy: string | undefined;
  removedElsewhere: boolean; newestReport: string | null; dataFirst: string | null; today: string; idPrefix: string;
  onAddValue: (v: AssetValue) => void; onRemoveValue: (index: number) => void;
  /** Save was pressed while a problem stopped it: the fields show every error */
  showAll: boolean;
}) {
  const a = row.asset, entry = row.entry;
  const panel = `${idPrefix}-panel-${a.id}`;
  const name = a.name.trim() || "the new asset";
  const saved = stored !== undefined && isLive(stored) ? stored : null;
  const late = afterNewestReport(a, newestReport);
  return (
    <>
      <Summary name={a.name} cls={a.asset_class} owner={a.owner} values={a.values}
        origin={entry !== null ? ORIGIN.copy : ORIGIN.app} tag={saved === null ? "not saved" : unsaved ? "unsaved" : undefined} />
      {entry !== null && (
        <p className="mt-1 text-xs text-zinc-400">
          The config's own value: {entryValue(entry)}.
          {entry.institution && <> A copy keeps no holder: where the config names {entry.institution}, the copy reads as
            entered by hand.</>}
        </p>
      )}
      {removedElsewhere && (
        <p className="mt-1 text-xs text-amber-400">
          Removed on another device while you were editing it: saving brings it back with your changes.
        </p>
      )}
      {saved !== null && !unsaved && (skippedWhy !== undefined ? <SkipNote why={skippedWhy} />
        : !countedAsSaved(saved, row.exported ?? undefined) && (
          <p className="mt-1 text-xs text-zinc-400">Saved; not in the dashboard yet: it counts after the next ingest.</p>
        ))}
      {!blocking && problems.length > 0 && (
        <p className="mt-1 text-xs text-amber-400">
          To fix (it does not stop other changes from being saved): {problems.map((p) => p.message).join(" ")}
        </p>
      )}
      {late !== null && (
        <p className="mt-1 text-xs text-zinc-400">
          Dated after the accounts' newest report ({fmtDate(newestReport!)}): it counts once the accounts report on or
          after {fmtDate(late)}.
        </p>
      )}
      <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1">
        <button type="button" onClick={onToggle} aria-expanded={open} aria-controls={panel} aria-label={`${open ? "Close" : "Edit"} ${name}`}
          className="inline-flex min-h-11 min-w-11 items-center text-xs text-blue-400 hover:underline md:min-h-0 md:min-w-0">
          {open ? "Close" : "Edit"}
        </button>
        <button type="button" onClick={onRemove}
          aria-label={entry !== null ? `Use the config's value again for ${name}` : `Remove ${name}`}
          className="inline-flex min-h-11 min-w-11 items-center text-xs text-blue-400 hover:underline md:min-h-0 md:min-w-0">
          {entry !== null ? "Use the config's value again" : "Remove"}
        </button>
      </div>
      {open && (
        <AssetFields id={panel} asset={a} onChange={onChange} classes={classes} owners={owners} problems={problems}
          dataFirst={dataFirst} today={today} idPrefix={idPrefix} onAddValue={onAddValue} onRemoveValue={onRemoveValue}
          showAll={showAll} />
      )}
    </>
  );
}

const BOX = "min-h-11 min-w-11 w-full rounded-md border bg-zinc-950 px-2 text-base text-zinc-200 sm:text-sm md:min-h-0 md:min-w-0 md:py-1";
const boxCls = (bad: boolean) => `${BOX} ${bad ? "border-red-500" : "border-zinc-700"}`;
const LABEL = "text-xs text-zinc-400";

/** one field's error, tied to its box by id */
const FieldError = ({ id, message }: { id: string; message: string | undefined }) =>
  message ? <p id={id} className="mt-0.5 text-xs text-red-400">{message}</p> : null;

/** the fields of an app asset: name, owner, class, liquid, the day it was acquired and its dated values. A field's error
 *  shows once the field has been left (touched) or Save was pressed (`showAll`): a new asset's empty form is not a page
 *  of red before anything is typed. The Save button's own state and the reason under it read every problem at once. */
function AssetFields({ id, asset, onChange, classes, owners, problems, dataFirst, today, idPrefix, onAddValue,
  onRemoveValue, showAll }: {
  id: string; asset: LiveAsset; onChange: (a: LiveAsset) => void; classes: AssetClasses; owners: readonly string[];
  problems: Problem[]; dataFirst: string | null; today: string; idPrefix: string; onAddValue: (v: AssetValue) => void;
  onRemoveValue: (index: number) => void; showAll: boolean;
}) {
  const uid = useId();
  const f = (field: string) => `${uid}-${field}`;
  const [touched, setTouched] = useState<ReadonlySet<string>>(() => new Set());
  const key = (field: Problem["field"], index?: number) => `${field}:${index ?? ""}`;
  // which field each control is, by its id: a control the person leaves is touched
  const nameId = `${idPrefix}-name-${asset.id}`;
  const fieldOf = new Map<string, string>([[nameId, key("name")], [f("owner"), key("owner")], [f("class"), key("asset_class")],
    [f("acquired"), key("acquired")], [f("liquid"), key("liquid")],
    ...asset.values.flatMap((_, i): [string, string][] => [[f(`date-${i}`), key("as_of", i)], [f(`amount-${i}`), key("value", i)]])]);
  const leave = (target: EventTarget) => {
    const k = target instanceof HTMLElement ? fieldOf.get(target.id) : undefined;
    if (k !== undefined && !touched.has(k)) setTouched(new Set(touched).add(k));
  };
  // the list's own problem (no value at all) is the values' once any of them has been touched
  const shows = (field: Problem["field"], index?: number) => showAll || touched.has(key(field, index))
    || (field === "values" && [...touched].some((k) => k.startsWith("as_of:") || k.startsWith("value:")));
  const err = (field: Problem["field"], index?: number) =>
    shows(field, index) ? problems.find((p) => p.field === field && p.index === index)?.message : undefined;
  const options = classOptions(classes);
  const debt = isDebtClass(asset.asset_class, classes);
  const acquired = asset.acquired ?? null;
  const question = acquiredQuestion(asset, dataFirst);
  const setClass = (cls: string) => onChange({ ...asset, asset_class: cls, liquid: isDebtClass(cls, classes) ? false : asset.liquid,
    values: signedFor(asset.values, asset.asset_class, cls, classes) });
  const setValue = (i: number, v: AssetValue) => onChange({ ...asset, values: asset.values.map((x, j) => (j === i ? v : x)) });
  const described = (field: Problem["field"], index?: number, extra?: string) =>
    [err(field, index) ? f(`${field}-${index ?? ""}-error`) : null, extra ?? null].filter(Boolean).join(" ") || undefined;
  return (
    <fieldset id={id} onBlur={(e) => leave(e.target)} className="mt-2 space-y-3 rounded-lg border border-zinc-800 p-3">
      <legend className="sr-only">{`The details of ${asset.name.trim() || "the new asset"}`}</legend>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor={nameId} className={LABEL}>Name</label>
          <input id={nameId} type="text" value={asset.name} autoComplete="off"
            onChange={(e) => onChange({ ...asset, name: e.target.value })} aria-invalid={!!err("name") || undefined}
            aria-describedby={described("name")} className={boxCls(!!err("name"))} />
          <FieldError id={f("name--error")} message={err("name")} />
        </div>
        <div>
          <label htmlFor={f("owner")} className={LABEL}>Owner</label>
          <select id={f("owner")} value={asset.owner} onChange={(e) => onChange({ ...asset, owner: e.target.value })}
            aria-invalid={!!err("owner") || undefined} aria-describedby={described("owner")} className={boxCls(!!err("owner"))}>
            <option value="">Choose who owns it</option>
            {owners.map((o) => <option key={o} value={o}>{o}</option>)}
            {asset.owner && !owners.includes(asset.owner) && <option value={asset.owner}>{asset.owner}</option>}
          </select>
          <FieldError id={f("owner--error")} message={err("owner")} />
        </div>
        <div>
          {/* the class's colour beside its label, so the box lines up with the other fields' boxes */}
          <label htmlFor={f("class")} className={LABEL}>
            Class
            {asset.asset_class && <span aria-hidden="true" data-testid="class-swatch"
              className="ml-1.5 inline-block size-2 rounded-full align-middle" style={{ background: classColor(asset.asset_class) }} />}
          </label>
          <select id={f("class")} value={asset.asset_class} onChange={(e) => setClass(e.target.value)}
            aria-invalid={!!err("asset_class") || undefined} aria-describedby={described("asset_class")}
            className={boxCls(!!err("asset_class"))}>
            <option value="">Choose a class</option>
            <optgroup label="Assets">{options.assets.map((c) => <option key={c} value={c}>{c}</option>)}</optgroup>
            <optgroup label="Debts">{options.liabilities.map((c) => <option key={c} value={c}>{c}</option>)}</optgroup>
            {asset.asset_class && ![...options.assets, ...options.liabilities].includes(asset.asset_class)
              && <option value={asset.asset_class}>{asset.asset_class}</option>}
          </select>
          <FieldError id={f("asset_class--error")} message={err("asset_class")} />
        </div>
        <div>
          <label htmlFor={f("acquired")} className={LABEL}>Acquired on (optional)</label>
          <div className="flex items-center gap-2">
            <input id={f("acquired")} type="date" value={acquired ?? ""} aria-invalid={!!err("acquired") || undefined}
              aria-describedby={described("acquired", undefined, f("acquired-hint"))}
              onChange={(e) => onChange({ ...asset, acquired: e.target.value === "" ? null : e.target.value })}
              className={boxCls(!!err("acquired"))} />
            {acquired !== null && (
              <button type="button" onClick={() => onChange({ ...asset, acquired: null })}
                aria-label="Clear the day it was acquired"
                className="min-h-11 min-w-11 shrink-0 text-xs text-blue-400 hover:underline md:min-h-0 md:min-w-0">Clear</button>
            )}
          </div>
          <p id={f("acquired-hint")} className="mt-0.5 text-xs text-zinc-500">
            Empty: held as far back as the data goes, at its first value.
          </p>
          <FieldError id={f("acquired--error")} message={err("acquired")} />
        </div>
      </div>
      <div>
        <label className="flex min-h-11 min-w-11 items-center gap-2 text-sm text-zinc-300 md:min-h-0 md:min-w-0">
          <input id={f("liquid")} type="checkbox" checked={asset.liquid && !debt} disabled={debt}
            aria-describedby={debt ? f("liquid-debt") : undefined}
            onChange={(e) => onChange({ ...asset, liquid: e.target.checked })} />
          Liquid: it can be spent soon
        </label>
        {debt && <p id={f("liquid-debt")} className="text-xs text-zinc-500">A debt is never liquid.</p>}
        <FieldError id={f("liquid--error")} message={err("liquid")} />
      </div>
      {question !== null && (
        <div role="group" aria-label="When it was acquired" data-testid="acquired-question"
          className="rounded-md bg-zinc-800/50 p-2 text-xs text-zinc-300">
          <p>Owned it before {fmtDate(dataFirst!)}? Nothing to do: it is counted at its first value back to then.</p>
          <p className="mt-1">
            Acquired on {fmtDate(question)}?{" "}
            <button type="button" onClick={() => onChange({ ...asset, acquired: question })}
              className="inline-flex min-h-11 min-w-11 items-center text-blue-400 hover:underline md:min-h-0 md:min-w-0">
              Set {fmtDate(question)} as the day it was acquired
            </button>
          </p>
        </div>
      )}
      <div>
        <p className={LABEL}>{debt ? "Amounts owed, each on its date" : "Values, each on its date"}</p>
        {debt && <p className="text-xs text-zinc-500">Enter what is owed; net worth counts it as a debt.</p>}
        <FieldError id={f("values--error")} message={err("values")} />
        <ul className="mt-1 space-y-2">
          {asset.values.map((v, i) => {
            const n = i + 1;
            // a debt's figure shows as the amount owed; one stored above zero shows below zero, so it reads as wrong
            const shown = v.value === null ? null : debt ? -v.value : v.value;
            return (
              <li key={i} className="flex flex-wrap items-end gap-2">
                <div>
                  <label htmlFor={f(`date-${i}`)} className={LABEL}>Date</label>
                  <input id={f(`date-${i}`)} type="date" value={v.as_of} aria-label={`Date of value ${n}`}
                    aria-invalid={!!err("as_of", i) || undefined}
                    aria-describedby={described("as_of", i, v.as_of > today ? f(`future-${i}`) : undefined)}
                    onChange={(e) => setValue(i, { ...v, as_of: e.target.value })} className={`${boxCls(!!err("as_of", i))} w-40`} />
                </div>
                <div>
                  <label htmlFor={f(`amount-${i}`)} className={LABEL}>{debt ? "Amount owed" : "Value"}</label>
                  <CurrencyInput id={f(`amount-${i}`)} label={`${debt ? "Amount owed" : "Value"} ${n}`} value={shown}
                    describedBy={described("value", i)} invalid={!!err("value", i)}
                    onCommit={(x) => setValue(i, { ...v, value: debt ? -Math.round(x * 100) / 100 : Math.round(x * 100) / 100 })}
                    onClear={() => setValue(i, { ...v, value: null })}
                    className={`${boxCls(!!err("value", i))} w-36 text-right`} />
                </div>
                <button type="button" onClick={() => onRemoveValue(i)} aria-label={`Remove value ${n}`}
                  className="min-h-11 min-w-11 text-xs text-blue-400 hover:underline md:min-h-0 md:min-w-0">Remove</button>
                {v.as_of > today && (
                  <p id={f(`future-${i}`)} className="w-full text-xs text-zinc-400">
                    {!isFirstValue(asset, v.as_of) ? `Takes effect on ${fmtDate(v.as_of)}.`
                      : acquired !== null && acquired > today ? `Takes effect on ${fmtDate(acquired)}, the day it was acquired.`
                      : "Counted at once: an asset counts at its first value until a later one takes effect."}
                  </p>
                )}
                <div className="w-full">
                  <FieldError id={f(`as_of-${i}-error`)} message={err("as_of", i)} />
                  <FieldError id={f(`value-${i}-error`)} message={err("value", i)} />
                </div>
              </li>
            );
          })}
        </ul>
        <button type="button" onClick={() => onAddValue({ as_of: today, value: null })}
          className="mt-2 min-h-11 min-w-11 rounded-md border border-zinc-700 px-3 text-xs text-zinc-200 hover:bg-zinc-800 md:min-h-0 md:min-w-0 md:py-1">
          Add a value
        </button>
      </div>
    </fieldset>
  );
}
