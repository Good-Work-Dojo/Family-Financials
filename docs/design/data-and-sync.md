# Loading data, versions and the family store

> Scope: how the dashboard loads the exports, survives a missing file or a version mismatch, tells an open tab it is
> out of date, and syncs the family's answers, preferences and manual assets. Describes the system as it is; verify
> names against the code (`app/src/data/DataContext.tsx`, `app/src/data/useSyncedDoc.ts`, `deploy/scenario_store.py`).

## Loading the exports

`loadAppData` fetches every file in `EXPORT_FILES` (`data/types.ts`) with `Promise.allSettled`; rejections map back to
files by position, so a new export file goes at the same index in both lists and needs a placeholder or a
`FATAL_FILES` entry. Only `FATAL_FILES` (manifest, transactions, assumptions, assets_timeseries) keep the full-screen
error, and only on the first load: a failed `reload()` keeps the last good data under a dismissible "Couldn't load the
latest data" banner (`notice-reload-failed`, with Retry). Any other failed file becomes a typed empty placeholder named
in `useData().missing`; every card that reads it is gated with `<RequireData files={[...]}>`
(`components/RequireData.tsx`; `MissingDataCard` is the bare card) instead of rendering a placeholder zero. A gated
section is a thin default export (the `RequireData` wrap) plus an inner page holding the hooks. The app never queries
the database.

No manifest is no failure: the ingest writes `manifest.json` last, so when the server has none (HTTP 404) or answers
with a page where it should be (a body starting with `<`, a static server's fallback), nothing has been built yet — the
first ingest has not run, is running, or refused the config. `loadAppData` throws `NothingBuiltError` for that, and
`DataProvider` shows what the app gives it as `nothingBuilt` (App.tsx: the first-run page with every step to take,
outside the shell; `docs/design/information-architecture.md`) instead of the error screen, and looks again every
`NOTHING_BUILT_RECHECK_MS` (30 s) and on its "Check now", so the app opens once the exports exist. Every other first-load
failure (another status, a body that does not parse, a network error, another fatal file absent) keeps the error
screen; a reload that finds no manifest after a good load is a failed reload like any other.

## Versions, both directions

The export contract: `SCHEMA_VERSION` (`etl/exports.py`) and `EXPECTED_SCHEMA_VERSION` (`data/types.ts`) move together
on any export shape change (`contract.test.ts`); a key or file is removed or renamed only in a major release;
`schemas.ts` and `types.ts` are two independent descriptions that change in the same commit. The current version, 23,
is the 1.0 baseline; versions 1–22 came before 1.0, and no released image writes one.

- **An OLDER export under a newer bundle** happens when a newer dashboard is served before an ingest rewrites the
  exports (an older ETL image can still write them after the new bundle is up). `loadAppData` therefore gives every
  key added after the baseline what "not there" means: a new export key gets its default there
  (`loadAppData.test.ts`). It also still reads an export written before 1.0, each key such an export lacks given the
  default its `with…` helper in `data/DataContext.tsx` states; `vendors.json` is an optional file whose 404 is its
  placeholder.
- **A NEWER export under an older open tab** (it would read the new keys the old way — split parents as ordinary rows)
  and **a newer BUILD** (most deploys change the app and bump no schema) raise ONE persistent banner,
  `notice-updated`: "The dashboard was updated — reload this page". Never an auto-reload: an edit may be in flight.

The build id: `app/build/versionPlugin.ts` hashes every emitted file of the bundle (`bundleId`) once, writes it into
`index.html` as `<meta name="ff-build">` and into `version.json` from the same value, and fails the build when it
cannot place it. `lib/buildId.ts` reads the meta tag once at load (`BUILD_ID`, "dev" under vite dev and vitest);
`data/useBuildCheck.ts` fetches `version.json` (no-store) when the tab returns to the foreground and after each data
reload, and latches true once the served id differs. It stays silent in dev, on a missing `version.json`, a 401 or any
failure. Caddy serves `/version.json` behind the sign-in with `no-store`, ahead of the `*.json` exports handle
(`deploy/Caddyfile`). A tab loaded from a page that predates the meta tag reads
"dev" and stays silent: reload such tabs once by hand after the deploy that ships the check.

## The family store

`deploy/scenario_store.py` (stdlib, the only browser-reachable backend, behind the same sign-in) keeps four documents
in the install's `state/family/`: `scenarios.json` (Second House what-ifs, at most `MAX_SCENARIOS`, last-write-wins), `answers.json`
(review answers), `prefs.json` (budgets, the target mix, asset-class targets, projection defaults, payroll facts,
saved views and reports, the modules switched on and the AI consent) and `manual_assets.json` (the assets entered in
the app, below). Writes are atomic, a new file renamed over the name, and no read or write goes through a link
(`deploy/safe_files.py`: the workers mount the folder read-only and the ingest reads the answers and the manual assets
from it). The store serves a fixed four-file map (`STORES`); the ETL's own `learned_rules.json` lives in
`state/engine/`, which the store does not mount. Every document's body is refused with a 400 that writes
nothing when it holds, anywhere (keys and sections the store does not know included), a number JSON cannot carry
(`NaN`, `Infinity`, `-Infinity`, or one too large for a float, which parses to infinity) or text UTF-8 cannot carry (a
lone surrogate, spelt `\ud800` or as its raw bytes): stored, the first leaves a document the browser cannot parse and
the second one the ingest cannot write to its database (`_parse_doc`, `encodes_as_utf8`).

**The client transport** is `useSyncedDoc`: server-first with a localStorage mirror; an optimistic commit, then a
debounced PUT, and a `pagehide` keepalive flush. On load the newer `updated_at` wins (or the store's own merge, the
`merge` option, for a store that merges item by item). `acked` is the copy the store last acknowledged, apart from the
optimistic one. A failed PUT marks the doc
local-only and retries the newest copy with backoff (`retryDelay`, capped at `RETRY_MAX_MS`); a document the store
refused outright (400, 413) is not sent again (`refused`) until a later edit gets through. Only the newest PUT's echo is
ever adopted.

**Answers** (`data/useAnswersStore.ts`): the store merges PUTs by key, the newer `answered_at` winning per key
(`merge_answers`), so two people can answer at once; the client re-reads on tab return (`resyncOnVisible`, `union`).
Both sides keep at most `MAX_ANSWERS` (500) one-off (txn) answers, dropping the oldest past that, and never drop a
vendor-scope answer, which does not count toward the cap (`capAnswers` = `cap_answers`, pinned by the shared fixture
`lib/answers.eviction.fixture.json`); a PUT carrying more than `MAX_ANSWERS` one-off answers is refused (400). The only
bound on vendor answers is the answers store's own body cap (`MAX_ANSWERS_BYTES`, 1 MiB; scenarios and prefs keep
theirs): about 4,800 vendor answers beside a full 500 one-off ones, about 5,300 alone, fewer with notes, so one per
vendor (~1,150) with a full set of one-off answers is about 316 KiB. Caddy caps every body at 2 MB, above every
document's cap, so the store is the one that answers an oversized save; the client has no size precheck of its own. A
document body over its store's cap but under `MAX_DRAIN_BYTES` (4 MiB) is read before the 413 goes out: Caddy streams
the body upstream, and a reply to a half-read request reaches the browser as a 502, which the client would retry as
"unreachable" instead of reporting. The cap is checked again on the merged doc; a save past it is a 413, which means
something is wrong, so Review and the register (`TxnTable`, whose row editor and the Transactions bulk editor write
answers) say `answersRefusalText`: the answers are saved in this browser only, won't reach the next data refresh, and
whoever maintains the dashboard should look. An unreachable store keeps the "for now" notice (`LocalOnlyNotice`; it
retries by itself). The answers store keeps no tombstones, so dropping a key locally is undone by the next echo.

**Prefs** (`data/usePrefsStore.ts`, `PREFS_SYNC`): last-write-wins per document (the object sections — budgets,
asset-class targets, projections, payroll — travel whole), except the two saved lists (`saved_filters`, `reports`) and
the sections kept whole, the target mix, the modules and the AI consent (below):

- a PUT carries a list only when this tab changed it (`keepOmitted`); the store keeps a list a PUT omits
  (`merge_prefs`; an explicit `[]` clears), so a tab on an older bundle cannot wipe the family's views;
- a changed list is first merged by id with the store's current copy (`mergeById`: a three-way merge against the copy
  this browser last heard; another device's additions survive, and a deletion elsewhere beats an edit here);
- the client re-reads on tab return (visible, focus, `pageshow`, back online);
- a keepalive flush has no time to ask, so it leaves changed lists out and the mirror keeps them to merge on the next
  load;
- known, accepted narrow windows (one owner): another tab of the same browser saving during this tab's save round
  trip, and a remote change landing between a list save's pre-read and its PUT.

**The target mix** (`target_mix`, `lib/targetMix.ts`): `null` (none saved) or the four buckets, each 0–100, totalling 100.
The client's `normalizePrefs` drops anything else with one warning (it reads as none saved) and `setTargetMix` refuses
it without a commit; the store's `validate_prefs` checks only the shape (null, or at most `MAX_TARGET_MIX_KEYS` numbers
from 0 to 100, never a bool, NaN or infinity). `merge_prefs` keeps a stored mix a PUT omits, as it keeps the lists, and
the client leans on that the way it does for the lists (`keepWhole` in `useSyncedDoc`): a PUT carries `target_mix` only
when this tab changed it since the copy the store last acknowledged, whole (last-write-wins for the section; `null`
clears), so a budget edit from a tab that loaded before another device saved a mix leaves that mix alone and the echo
hands it over. A section this tab changed stays marked as changed until the store answers the newest PUT, one that
carried its current value (an older PUT's late answer confirms nothing: the store may hold a newer one's value), so
every PUT carries it meanwhile, even one where it is back at the acknowledged copy: switched and switched back before
the first save is answered, the second save still sends it (the store holds the first save's value), and a failed
save keeps the mark for the retry. A marked section is this tab's in every merge too (a reconnect after edits made
offline, a re-read, an echo while an edit waits), so it goes out and its answer clears the mark. The load, an echo arriving while an edit waits and a re-read
keep this tab's copy only when it changed it.
A tab on a bundle that predates the section drops it on load; its PUTs omit it, so the store keeps the mix. Rolled back,
the older store accepts and stores the section verbatim (it passes unknown sections) but does not keep it on an omitting
PUT, so the first prefs save after the rollback ends a saved mix (copy `state/family/prefs.json` aside before rolling back);
the card then starts from the suggestion again and says it is not saved. The same holds for the seconds of a deploy in
which a new tab could reach the old store. Nothing else is affected.

**The modules and the AI consent** (`modules`, `ai`) are kept whole by the same rule (`PREFS_KEPT_WHOLE` = the target
mix, the modules, the AI consent): a PUT that omits one keeps the stored value when it is an object that passes the
section's own check, so a tab on a bundle that predates them can neither switch a module off nor withdraw a consent;
a PUT that carries one replaces it, and `modules: {}` clears the switches. `validate_prefs` checks the shape only:
`modules` is an object of at most `MAX_MODULES` (16) ids of at most `MAX_MODULE_ID_LEN` (40) characters, each `true` or
`false`; `ai` holds exactly `enabled` (a boolean), `consent_policy` (a whole number, never a boolean, or `null`),
`consent_scope` (text of at most `AI_SCOPE_MAX` (200) characters, or `null`: `docs/design/ai.md`) and `consented_at`
(a string or `null`). The rollback note above holds for both: an older store passes them as unknown
sections and drops them on the first PUT that omits them. The client keeps both whole the same way as the mix
(`KEPT_WHOLE_KEYS` in `lib/prefs.ts`, `PREFS_SYNC`'s `keepWhole`; `tests/test_scenario_store.py` pins the list to
`PREFS_KEPT_WHOLE`): a PUT carries either only when this tab changed it. Its `normalizePrefs` keeps `modules` as every
id of at most `MAX_MODULE_ID_LEN` characters whose value is `true` or `false`, the ids it knows (`lib/modules.ts`
`MODULE_IDS`) first, at most `MAX_MODULES` (the store's own bounds, pinned equal by the same test), and carries an id
it does not know through every save, ignored for effect: a module switched in this tab sends a later bundle's
switches along with its own, so this bundle can never erase one; and `ai` as its three fields, anything malformed reading as off
(`AI_OFF`). Nothing in the dashboard reads `ai` yet; `modules` decides which pages show
(`docs/design/information-architecture.md`, "Switchable pages").

Caps: the store takes a prefs body up to `MAX_PREFS_BYTES` (64 KB, checked again on the MERGED doc: `MergedTooLarge`
→ 413) and a list entry up to `MAX_SAVED_ENTRY_BYTES`; the client keeps its doc under `MAX_PREFS_DOC_BYTES` (56 KB),
an entry under `MAX_SAVED_ENTRY_CHARS` and a list under `MAX_SAVED`, so a full doc is refused in the UI ("Settings are
full — delete a view on Data first", `saveRefusalText`) instead of failing every later save. Deleting is never refused.

**Manual assets** (`manual_assets.json` at `/api/manual-assets`: a dash and no `.json` suffix, which the Caddyfile's
`*.json` exports matcher would take): `{version: 1, updated_at, assets: [{id, name, owner, asset_class, liquid,
acquired?, values: [{as_of, value}], updated_at, deleted_at?}]}`, a value in dollars, negative for a debt. The ETL
reads the file read-only on ingest (`etl/manual_assets.py`: the manual assets net worth counts) and never writes it.
A save that changed the stored document asks for an ingest without a fetch (the ingest marker,
`docs/design/information-architecture.md`), so the change reaches net worth without waiting for a refresh.

- *The store checks the shape only* (`validate_manual_assets`; a 400 saying what is wrong): `version` exactly 1, never a
  boolean; `updated_at` the stamp below, or the empty document's `1970-01-01T00:00:00Z` (the merge keeps the newer of
  two documents' stamps, so any other string could win it for ever); `assets` a list of objects with unique ids. An
  id is 1–64 characters, runs of letters or digits of any script joined by single dashes, none at either end, and its
  own lowercase — the shape of every config entry's id (`networth.manual_slug` of its name), so an app asset can carry
  one to override that entry; `is_manual_id` here and `etl/manual_assets.py` hold the same rule. A name is at most 80
  characters as sent (padding counts) and not all spaces; the owner and the asset class are non-empty strings of at
  most 80 characters; none of the three holds a control character (U+0000 to U+001F, or DEL: `has_control`, the same
  rule in the engine, which leaves out an asset whose name holds one); `liquid` is a boolean; `acquired` is absent,
  `null` or a day (`YYYY-MM-DD`, a real date); each value is `{as_of: a day, value: a number from -1e12 to 1e12, never a
  boolean}`; `updated_at` and `deleted_at` (absent or `null` while the asset is live) are the stamp the dashboard
  writes, `YYYY-MM-DDTHH:MM:SS.sssZ` in UTC with the milliseconds always there, one width so that string order is time
  order. Nothing nests more than `MAX_MANUAL_DEPTH` (32) levels deep, the document itself the first, so the engine's
  loader can always read back what the store wrote. A key the store does not know passes verbatim on a live asset, as
  on an answer, and the engine ignores it. A removal (below) needs only its id, name and stamps; an owner, class, flag
  or values it carries are checked all the same.
- *The engine checks the meaning* against the household and leaves an asset out with a `manual_assets.entry` warning,
  never a figure: an owner that is not the household's, a class it does not know, a debt marked liquid, `acquired`
  after the first value, a name that holds a control character or is an account's or another counted asset's. The
  store knows nothing of the household, so it stores such an asset as sent; the engine skips it at every ingest, and
  the warning in the quality report is where it shows. A counted asset in a debt class that states a value above
  zero is counted as stated, with a `manual_assets.debt_sign` warning ("manual asset 'id' from the app | in
  accounts.yaml is in the debt class C but states a value above zero, which net worth counts as an asset: a debt's
  value is negative (in the dashboard, enter the amount owed)").
- *Caps:* at most `MAX_MANUAL_ASSETS` (50) live assets and `MAX_MANUAL_VALUES` (120) values per asset, refused with a 400
  naming the cap and never evicted (an asset dropped to make room would leave net worth), in a body of at most
  `MAX_MANUAL_BYTES` (512 KiB), which holds a document at every count's cap with realistic field lengths (about
  340 KB) and stays under the engine's loader limit (1 MiB), so whatever the store writes is read.
- *The merge* (`merge_manual_assets`): a union by id; per id the entry with the newer `updated_at` wins whole, a tie
  going to the PUT, so two tabs that each add an asset keep both. The document's `updated_at` is the newer of the two,
  the entries are stored sorted by id, and the 200 echoes the merged document. A PUT never removes by leaving an
  asset out: a removal is a tombstone, the entry with `deleted_at` set, stored with its id, name, `updated_at` and
  `deleted_at` only (`TOMBSTONE_KEYS`: whatever else the tab sent, its values included, is dropped, so a removed asset
  does not keep 120 values for 90 days), which beats an older copy from either side, so a tab still holding the asset
  cannot bring it back, while entering the id again later beats the tombstone. Tombstones do not count toward the 50:
  at most `MAX_MANUAL_TOMBSTONES` (200) are kept, and one whose `updated_at` is more than `TOMBSTONE_KEEP_DAYS` (90)
  days before the merge is dropped once it has beaten the copy it removes, so a tab away longer than that can bring a
  removed asset back. Both go by `updated_at`, the stamp every merge is decided by (the removals stamped longest ago
  go first); `deleted_at` only marks the entry as a removal, so a removal re-stamped recently stays its 90 days. A
  merge that would hold more than 50 live assets (a tab adding one to a full store it has not heard from) is a 400
  naming the cap that writes nothing, as validation's refusal of a document past it is; a 413 (`MergedTooLarge`) is
  only ever about bytes, a body or a document as stored over the cap. A stored entry the store would refuse (a hand
  edit) is left out of the merge with a stderr line. An incoming asset whose `updated_at` or `deleted_at`, or a document whose
  `updated_at`, is more than `MAX_CLOCK_AHEAD_S` (24 hours) ahead of the store's clock is a 400 that writes nothing
  ("check this device's clock"): a device whose clock runs ahead would otherwise win every later merge, and its
  removals would never expire.
- *The client* (`data/useManualAssetsStore.ts`, document logic in `lib/manualAssets.ts`; the mirror
  `ff.manual-assets.v1`): `useSyncedDoc` with the echo adopted, a re-read on tab return, no debounce (a save is an
  explicit Save, never a keystroke) and its `merge` option, so the load and a reconnect merge the mirror with the
  store's copy by id as the store does (`mergeManualAssets`: the newer `updated_at` per asset, the store's on a tie)
  and push what the store lacks, instead of the newer document winning whole. An entry only the mirror holds is kept
  only while its `updated_at` is within `TOMBSTONE_KEEP_DAYS` (90, pinned to the store's) of the load: an older one may
  be an asset another device removed so long ago that the store has forgotten the tombstone, and sending it back would
  bring the asset back; the mirror is rewritten with the merged copy. `normalizeManualAssets` keeps only the entries
  the store would take (a save holding one it refuses would be refused whole), a tombstone as the store keeps one, and
  any key it does not know on a live asset. A Save sends the whole document (`forSave`) built over the copy the store
  last ACKNOWLEDGED (`useSyncedDoc` `acked`: the load's copy, an adopted echo or re-read): every entry left as it was
  goes verbatim, stamps included, and every changed one is trimmed as the engine trims (Python's `strip`), rounded to
  the cent and stamped with the save's instant, so the newest save of an asset wins it anywhere; a removal of an asset
  the store never held is dropped. A refused save is not a saved one: the optimistic commit keeps it in this browser's
  copy and mirror, but not in `acked`, so the card still calls it unsaved, Revert sends the acknowledged copy back (the
  refused one is not sent again on the next load), and a later save re-stamps the changed entries (a clock that was
  ahead, once corrected, saves). `useSyncedDoc` reports a refusal's own words (`refusedReason`, the reply's `error`) and
  counts the PUTs the store answered or refused (`accepted`, `refusals`, the newest PUT's only), which is how a Save
  learns its outcome. The unsaved draft lasts the session (`data/useManualAssetsDraft.tsx`, above the routes) and is
  carried over every newer acknowledged copy (`rebaseDraft`: an entry the draft did not touch takes the newer copy; one
  it edited that another device removed meanwhile is kept and named, `removedElsewhere`).
- *An app copy of a config entry* (Edit a copy, Settings) carries the entry's id and nothing else to mark it: the
  exports say which entries an app asset hides (`accounts.json` `manual_overridden`, schema 21), so an asset is a copy
  while its id is one of those or one the exports still count as the config's (a copy not ingested yet, or one the
  engine left out; `configEntryOf`). An entry since deleted from `accounts.yaml` is in neither list, and its old copy
  is a plain app asset with Remove. Taking the config's value again is a tombstone for the id, after which the entry
  counts again (`etl/manual_assets.py` `effective`). A `from_config` key an earlier bundle wrote is tolerated and
  dropped on that asset's next save. The copy keeps no holder (`institution` has no key in the document).
- *What the editor checks before a save* (`validateDraft`, each problem tied to its asset and field): the store's shape
  (the id, a name of 1 to 80 characters once trimmed with no control character, an owner and a class of at most 80
  characters with none either, the liquid flag, real days for `acquired` and every value, 1 to 120 values within a
  trillion dollars either way, at most 50 live assets, a document of at most 512 KiB; `tests/test_scenario_store.py`
  pins its limits to the store's) and the engine's meaning (the owner one of `assumptions.owners`, the class one of
  `assumptions.asset_classes`, a debt never liquid, `acquired` not after the first value, a name that is no registry
  account's and no other counted asset's — the config entries hidden now included, as each counts again once no live
  asset of its id is left — compared as the engine compares, Python's `strip` then `casefold`: `nameKey`). Its own
  rules: one value per day (the engine would keep the later of two silently) and a debt's value below zero (the engine
  counts one above zero as an asset, with a `manual_assets.debt_sign` warning). A problem the acknowledged copy already
  had on an asset the draft leaves alone is that row's warning and blocks nothing; one the draft makes blocks Save.
  The engine stays the judge: an asset it leaves out shows its `manual_assets.entry` reason on the card.

**Per-device flags** never reach the store: `lib/localFlagStore.ts` keeps the essential toggles (`efund-overrides`) and
the theme in localStorage (read fresh, written through, other tabs told by the `storage` event); privacy mode is its
own cached flag (`docs/design/privacy.md`).

## The store's other routes: the sign-in and the mailbox

Besides the documents the store answers the sign-in and the refresh; their rules are `docs/design/platform.md`
("The sign-in"). `POST /auth/login` (a passcode, form or JSON) opens a session cookie, `POST /auth/logout` ends it
server side, `GET /auth/check` is the question Caddy asks before every protected request (204, 302 to `/login`, or
401), `GET /auth/status` says whether any passcode is set up, `GET /healthz` answers `ok`. Every `/api/*` route also
asks for a live session (401 without one), and every write carries an `Origin` equal to the request's own (403
otherwise). The sessions live in `state/auth/`, never beside the documents.

`/api/refresh` is no document: the store writes a request into the mailbox (`state/refresh/`), the one folder it
shares with the two workers, and reads back the status they write and the schedule the scheduler wrote
(`schedule.json`), whose zone every daily cap counts days in. `docs/design/information-architecture.md` ("The server
side") has the mailbox's files and who writes each.

**Signed out** (`data/session.ts`): a store call answered 401 marks the page signed out. `useSyncedDoc` then holds
every document's edits (in memory and in this browser's copy, no retry, no re-read) and `DataContext` shows one banner,
"Signed out — sign in to save your changes", with a full link to `/login`; when a store call succeeds again each held
edit is sent once. An export read answered 401, or redirected to `/login`, sends the browser to the sign-in page.
Sign out (Settings, `data/signOut.ts`) asks first when an edit has not reached the store, posts `/auth/logout`, then
removes this browser's copies of the four documents (their `localKey`s) and goes to `/login`; the theme, privacy mode
and the essential toggles stay, being the device's.
