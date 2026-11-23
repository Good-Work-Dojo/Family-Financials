# The AI step: what is sent, the stored answers, the apply step and the switch

> Scope: the optional AI categorization end to end, for a contributor: the one candidate predicate, the never-send
> filter and its tests, the request and the providers, the stored answers and the run log, the apply step's place in
> the enrich order, `ai_status.json`, the consent the store, the ETL and the dashboard read alike, the refresh step and
> its markers, `ff ai`, and the guards that keep the offline path offline. The household's guide is
> `docs/ai-categorization.md`; the config keys are `docs/configuration.md` (`household.yaml` `ai`). Describes the
> system as it is; the code truth is the module docstrings of `etl/ai/` and `etl/enrich/ai.py`.

## The pieces

| Where | What |
|---|---|
| `etl/ai/policy.py` | `POLICY_VERSION`, the instruction text, the name, digit, address and length tests (`text_reason`), the payment-app marks, `household_tokens`. Pure. |
| `etl/ai/candidates.py` | `Predicate` / `is_candidate` (which rows are asked about), `vendor_key`, `candidates`, `withhold`. Pure. |
| `etl/ai/request.py` | `answer_set`, `build_request` (the body, provider-neutral), `serialise`, `NothingToAsk`. Pure. |
| `etl/ai/cache.py` | the AI store's files, `check_answer` (the one gate between an answer and the store), `consent`, the run log, the lock, `last_run`. Offline, no clock. |
| `etl/ai/providers.py` | the one networked AI module: `read_key`, `endpoint`, `ask` through the official `anthropic` / `openai` SDKs (imported inside the call only). |
| `etl/ai/cli.py` | `python -m etl ai preview\|run\|reset`. |
| `etl/enrich/ai.py` | the ingest's half: `apply`, `set_category`, `status` (`ai_status.json`), `check` (`ai.applied`). Offline. |
| `deploy/refresh.sh` | `categorize_step` between the fetch and the ingest; `ai_reset_step` |
| `deploy/refresh_state.py`, `deploy/refresh_runner.py`, `deploy/scenario_store.py` | the markers `ai_wanted` / `ai_reset_wanted`, their claim, the store's `"ai"` / `"ai_reset"` modes and its check of `prefs.ai` |
| `app/src/lib/aiCard.ts`, `sections/settings/AiCard.tsx`, `AiConsentDialog.tsx`; `lib/prefs.ts` `aiPrefs` / `withAi`; `lib/categorySource.ts` | the Settings card and its consent dialog; the family's `ai` section; the AI chip and the "Filed by" facet |
| `ff` | `ai preview` and `ai reset` with no network, `ai run` on Docker's bridge network |
| `tools/demo/behaviours/ai.py` | the demo's planted vendors and `truth.json` `figures.ai` |

## One predicate

`candidates.Predicate` decides which rows the AI step is about, and the preview, a run and the apply step all use it,
so what is shown, what is sent and what is filed cannot drift apart. A row is a candidate when it is a purchase filed
by the fallback (`category_source` `fallback`) under the reserved uncategorized pair; not a split parent or part; not
internal; flagged for review for no reason but `uncategorized` (`etl/enrich/categorize.py` `UNCATEGORIZED_REASON`,
set only where the fallback raises the flag); untouched by the transfer stage (no counter hint, transfer group or
match status); no payment-app or person-to-person row (`policy.is_payment_app` on the raw descriptor and the vendor,
plus the household's `venmo_patterns` and `zelle_bank_types`, on every account); of a vendor the learner may learn and
not "Unknown"; on a credit card unless `ai.accounts: all`. The module docstring is the list.

`vendor_key` is the key a vendor is asked about and stored under: the vendor after the household's vendor renames,
NFKC, case-folded, control characters dropped, whitespace squashed.

The AI command makes its own offline pipeline pass (`rules_cli.parsed_pipeline` + `enrich`, the family's answers,
learned rules and overrides on) with `Pipeline.ai_apply = False`, so a vendor the stored answers already file is still
a candidate there (a `--refresh` run, and a preview that shows what the household's own filing leaves alone).

## The never-send filter

`withhold` keeps, per vendor key, only what `policy.text_reason` allows and what has at least `ai.min_seen` distinct
candidate rows; a withheld key counts under the first reason of `policy.REASONS` (`name`, `digits`, `address`,
`length`, `seen`) and only counts leave it, never a name. The name tokens (`household_tokens`) are the adults, the
children, the surname, the joint label, the household's own `name` less `NAME_STOP_WORDS`, `own_name_tokens` and
every word of `ai.never_send`; text is compared `fold`ed (NFKC, case-folded, combining marks and format characters
dropped, a few letters transliterated). A token of four letters or more matches inside a word (so its possessive
without an apostrophe too), a shorter one only as a whole word or that word with an "s" after it ("bens" for Ben, as
banks print a possessive). Digits: three in one run of digits and separators, three in one word, three across two neighbouring
words that each hold one, or a month beside a day. The answer set (`request.answer_set`) applies the same test to
every category and subcategory name and drops the type-driven categories and the reserved fallback subcategories.

`build_request` checks every vendor and every answer-set name once more against `text_reason` with the household's
tokens and raises (naming the reason, never the text) if one would be sent: a second line behind `withhold`. An
empty answer set raises `NothingToAsk` and no request is made.

The tests: `tests/test_ai_never_send.py` builds a few thousand generated descriptors per run (names in every case and
position, inside words, with accents, invisible characters and punctuation; check, phone, store and card numbers;
dates; payment-app and own-name rows) across dozens of invented households, and checks with an oracle of its own that
the serialised body holds no name token, no three-digit run, no amount, date, account id or number, no address mark,
no payment-app vendor, and that the preview prints exactly the body; a non-vacuity test pins minimum counts per
reason. `tests/test_ai_candidates.py` holds the predicate's and the filter's cases one by one. A change to what is
sent (a field, a filter, the answer set's rule, the instruction text, the provider set) moves `POLICY_VERSION`.

## The request and the providers

The body is `{policy_version, instructions, answer_set: [{category, subcategories}], vendors}`; `ai preview` prints
`serialise` of it over every sendable vendor, and a run sends the same body over at most `ai.caps.vendors_per_run` of
them (`cli.choose`: the unanswered ones in the preview's order; with `--refresh`, then the answered ones, oldest
first). The system prompt is the instruction text and the one user message is the serialised body; the model, an
output limit (`providers.max_tokens`) and the answer's JSON schema (`ANSWER_SCHEMA`) go with it. Anthropic:
`messages.create` with `output_config.format` (structured output; no thinking, sampling or streaming setting).
OpenAI: `chat.completions.create` with a strict `json_schema` `response_format`, at `ai.base_url` or OpenAI's own.

`providers.py` bounds a run: the key and the address passed to the constructor, every `ANTHROPIC_*` and `OPENAI_*`
variable removed from the environment first (`_quiet`: no credential, custom header, organization, project or base
URL is read from one),
`TIMEOUT` 30 s per attempt, the SDK's own retries limited to `MAX_RETRIES` 2, a wait between them of at most
`RETRY_WAIT_CAP` 5 s whatever the provider asks, `DEADLINE` 90 s in all, no redirect followed, an answer of at most
`MAX_ANSWER_BYTES` (1 MiB) asked for and read unencoded, the SDKs' debug logging off. A failure is reduced to its
class and HTTP status, never its text. `endpoint` sends Anthropic to its own address only; OpenAI to the configured
`base_url` (`validate.base_url_problem`: `https://`, or `http://` to a loopback host, no credentials, query or
fragment) or its own. The test seam `FF_AI_TEST_ORIGIN` accepts exactly `http://127.0.0.1:<port>` and nothing else;
production never sets it. `tests/fake_llm.py` is the stand-in both providers are tested against.

`cache.check_answer` keeps an item only when it is an object of exactly vendor, category, subcategory (which may be
left out) and confidence, its vendor one sent, the first for that vendor, its pair one of the answer set (or the category `"none"`), its
confidence a finite number from 0 to 1; everything else is dropped and counted by `DROP_REASONS`. An answer that is
not an object holding an `answers` list stores nothing.

## The AI store

`store/ai/` (`Paths.ai_store_dir`, made 0700), written by `ai run` and `ai reset` only, each under a non-blocking
flock on `run.lock` (0600):

- `vendor_categories.json` (0600, whole, link-proof, atomic): `{version: 1, policy, provider, model, entries:
  {<vendor key>: {category, subcategory, confidence, asked_at}}}`. Every valid answer is stored with its confidence;
  `"none"` is stored as a null category (not asked again, never applied). A run under another policy, provider or
  model starts the file afresh. `ai reset` removes it.
- `runs.jsonl` (0600): one line per run that sends, written with outcome `asked` before the request leaves (so a run
  that never returns counts against the day) and replaced by its final line: time, provider, model, policy, outcome,
  the counts and the vendor keys sent. The newest `LOG_KEEP_BYTES` (1 MiB) are kept; `ai reset` keeps it.
  `runs_per_day` counts its lines on the household's day; a log that is not a regular file stops the run.
  `cache.last_run` reads only lines of a run's shape, counts only.

## The apply step and precedence

`ai_mod.apply` runs in `Pipeline.enrich` after the internal resync and before the splits: after every pairing phase
(so the generic matcher's `Categorizer.places` question never reads a category it set) and before the review answers
and the overrides, which re-stamp what they reach and so outrank it. It does nothing unless `cache.consent` is `on`
and an answers file holding at least one entry is stored (an empty file, a link, a folder or a file that is not
answers counts as none). Each stored entry, re-keyed through the vendor renamer and its category through the category
renamer, is skipped as `low_confidence` (null, or below `ai.confidence`), `stale_category` (not a pair of today's
answer set nor such a category alone, never the fallback category alone) or `not_candidate` (no candidate row now);
otherwise every candidate row of the vendor takes `set_category`: `category_source` `ai`, and the review flag
cleared only when its reason is `uncategorized`. Nothing writes answers, learned rules or vendor rules
(`learned.derive` reads the family's answers alone and runs earlier). An answer for an AI row in the dashboard is an
ordinary pending answer (the overlay stamps `answer`), and the next ingest files the row by it.

`tests/test_ai_apply.py` covers precedence (a vendor rule, a learned rule, an answer for the row or the vendor, an
override each outrank a stored answer), that nothing reaches `answers.json` or `learned_rules.json`, the skip
reasons, renames, and that with the step off no stored answer is even read.

## `ai_status.json`

Written by every ingest, on or off, counts only (never a vendor or the key), in `exports.AI_STATUS_KEYS` order:
`configured` (a usable key file is stored: lstat only, never opened), `enabled` (consent `on`), `provider`, `model`,
`endpoint_host` (host[:port] of `ai.base_url` for an OpenAI-compatible service, else null), `policy_version`, `scope`
(`policy.consent_scope`: below), `consent_policy` (the version last agreed to, or null), `caps`, `last_run` (`{at, outcome, sent, answered, cached}`
or null), `answers_stored`, `applied {vendors, rows}`, `skipped {low_confidence, stale_category, not_candidate}`,
`preview {vendors, withheld}` (what `ai preview` would show, over the rows as they stand without this step). Export
schema 23 added it and `ai` to `CATEGORY_SOURCES`; the app's placeholder for an older export is `AI_STATUS_NONE`
(`data/types.ts`). While answers apply, the quality report carries one `info` line, `ai.applied`. With the step off
or nothing stored, every other export is byte-identical to the same ingest without the feature
(`test_the_demo_exports_with_the_step_off_or_nothing_stored_differ_in_ai_status_alone`).

## Consent, read the same three times

The family's switch is `prefs.ai = {enabled, consent_policy, consent_scope, consented_at}` in
`state/family/prefs.json`, exactly those four keys: `enabled` a boolean, `consent_policy` a whole number (not a
boolean) or null, `consent_scope` text of at most 200 characters (`policy.SCOPE_MAX`, `AI_SCOPE_MAX` in the store and
the app) or null, `consented_at` text or null. The store refuses any other shape on save (`scenario_store._check_ai`,
`AI_KEYS`); the ETL reads anything else as off (`cache._prefs_ai`, `CONSENT_KEYS`;
`test_the_consent_keys_are_the_family_stores` pins the sets and the limit equal across the three); the dashboard reads
anything else as `AI_OFF` (`lib/prefs.ts` `aiPrefs`; a policy written `1.0` is the one shape JSON cannot tell apart
in the browser: it reads as on there and off in the store and the ETL, so the step stays off).

The scope (`policy.consent_scope`, exported as `ai_status.json` `scope`) is who receives the vendor names and which
accounts' rows are asked about: `"<provider>||<accounts>"` for a provider's own API, where the model is not in it (the
same recipient), and `"openai|<host[:port]><path>|<model>|<accounts>"` for a service at `ai.base_url`, where a
gateway's path or model can choose the company that receives the names (never the scheme, user name or query; `|` and
`%` percent-encoded in a field). One longer than `SCOPE_MAX` keeps `|<accounts>` and has the rest cut to fit, ended by
`~` and the first `SCOPE_HASH` (12) hex digits of the SHA-256 of the uncut rest. `cache.consent` is `on` only for
`enabled` true, `consent_policy == POLICY_VERSION` and `consent_scope` equal to the current scope; `stale` for another
whole number or another scope; `off` otherwise; a run also needs a key. A run reads it twice: before it takes the lock,
and again just before it logs the run and sends, after the offline pass that reads the household's data, so a switch
turned off meanwhile sends nothing (exit 0, "Nothing was sent", no run counted). So a household.yaml edit to `ai.provider`,
`ai.base_url` or `ai.accounts` (or to `ai.model` with a `base_url`) turns the step off until the family agrees
again, and the card shows it as `stale` (`aiCard.ts` `agreed`). `ai` is one of the prefs sections a tab writes
whole (`KEPT_WHOLE_KEYS`), so a tab from an older bundle cannot erase or half-write it. The card's Turn on writes the
version and the scope `ai_status.json` names, so a household agrees to what it was shown.

## The refresh step and the markers

`refresh.sh` runs `categorize_step` between the fetch and the ingest. When nothing (not even a link) is at the key's
name (`ai_key_path`, the `Paths` order) it returns at once: no process starts, no socket opens. Otherwise it runs
`timeout -k 10 120 python -m etl ai run --from-refresh` (`AI_TIMEOUT_S`), which prints nothing while the step is off
and one line of counts otherwise. It is never fatal: the refresh's exit code, streak and status are untouched. Exit 3
(the key refused) sends one alert per streak (`.ai_alert_state` in the engine folder); every failure logs how many
runs in a row have failed (`.ai_fail_state`; 124 and 137 also say it was stopped); exit 0 clears both.

The dashboard asks through the mailbox, as for an ingest: `POST /api/refresh {"mode": "ai"}` (Turn on) or
`{"mode": "ai_reset"}` (Forget) touches `ai_wanted` or `ai_reset_wanted` and the ingest marker, under the ingest's
Origin rule, session and daily cap (`MAX_INGEST_PER_DAY`); Turn off writes `enabled: false` and asks for a plain
ingest. `ingest_due` treats a due AI marker as an ingest request (same settle and cap); the refresher claims the due
markers with the run (`status.ai_handled_at`) and starts `refresh.sh ingest` with `FF_AI_RESET` / `FF_AI_RUN`, which
forgets the answers (no key needed) and then runs `categorize_step` before that ingest. The store never reaches
`store/ai/` or the key. `docs/design/information-architecture.md` ("The server side") has the ingest marker's rules.

## The commands and their exit codes

| Command | Network | Exit |
|---|---|---|
| `python -m etl ai preview` | none | 0; 2 for a config with problems or no subcommand |
| `python -m etl ai run [--refresh] [--from-refresh]` | the provider | 0 answers stored or nothing to do (off, a cap, no vendor, every vendor answered, the lock held, no category to choose from); 3 the provider refused the key; 1 any other failure (nothing stored); 2 a config with problems |
| `python -m etl ai reset` | none; needs no config | 0 forgotten or nothing stored; 1 locked or not removable |

`ff ai preview` and `ff ai reset` run in the ETL image with `--network none`; `ff ai run` on the bridge network, as
`ff init` does for the bank feed. In the stack the workers run on Docker's default network (the bank feed's), so
`127.0.0.1` in `ai.base_url` is the worker container itself.

## Offline stays offline

`tests/test_offline_imports.py` imports `etl.pipeline` in a fresh interpreter and ingests `examples/minimal` and the
demo household `full`, then asserts that none of `urllib.request`, `http.client`, `ssl`, `socket`, `requests`,
`httpx`, `httpx2`, `anthropic`, `openai`, `certifi`, `etl.simplefin`, `etl._net` or `etl.ai.providers` was loaded;
the same for importing `etl.ai.policy`, `candidates`, `request`, `cache` and `cli`. A new import on the offline side
that pulls one of them in fails here. `tests/test_ai_apply.py` patches `socket.socket` to show that, with the step
off, `ai run --from-refresh` opens no socket, and `tests/test_refresh_sh.py` traces the script to show that with no key
no AI process starts.

## The demo and the stack

The demo household (`full`, and the profiles that share its rows) plants vendors on one card: two that are asked
about, one withheld for a member's name, one for a number, one seen once, a PayPal row and an internal transfer; its
`truth.json` `figures.ai` states each and why, and `tests/demo_checks.py` compares `ai_status.json` with it. Its
`household.yaml` has an `ai:` block and no key, so the step stays off.

The gate's stack stage (`tools/verify/stack_ai.py`, around the browser run in `stack.sh`) stores an invented key,
runs the stand-in on the refresher's own loopback, switches the step on through the store's real requests, checks that
exactly truth's sendable vendors were asked about and filed (and the body held nothing withheld), that the browser
shows their chips, then switches it off and forgets, and checks the transactions export is the AI-off one byte for
byte. No request leaves the machine.
