# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Config validation: every problem the config files have, found in one run, each naming its file, the key path in it,
what is wrong and how to fix it.

  Problem      one problem, rendered `file: key.path: what is wrong — how to fix it` (`file: what — fix` when the
               problem is the file's as a whole)
  Problems     the collector: Problems or text in that form (Problem.parse), iterated sorted by file then key path,
               each once
  ConfigError  a ValueError carrying every problem, one per line: Config raises it once it has loaded and checked
               every file it could
  KEYS         every key a validator knows: file, key path pattern, type, default and meaning. The unknown-key checks
               read it, and docs/configuration.md lists exactly these keys (tests/test_validate.py pins the two)
  PRESETS_DIR  the config's folder of the household's own CSV presets, whose keys are the preset engine's, not KEYS

A key path pattern writes `<id>` (or another `<name>`) for a key the household chooses and `[]` for any item of a list.
Here live the file loading problems, each file's top level (a mapping, overrides.yaml a list; an optional `version`
of 1; the type of each block a rule file holds), the checks of household.yaml and accounts.yaml, and those of the rule
files beyond their compilers: unknown keys everywhere, the names a rule, a bank label, an override or a budget gives
checked against the taxonomy, every regex, the earners (Roster), the overrides' match and set, the assumptions' values.
Config (etl/config.py) runs them beside its own checks and the collecting compilers (rules, splits, renames,
transfers), which return text in the same form; an entry whose unknown key is reported here is left out of its
compiler. `validate` runs Config's whole collection and returns it instead of raising."""
from __future__ import annotations

import datetime as dt
import difflib
import ipaddress
import math
import os
import re
from collections.abc import Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from yaml.constructor import SafeConstructor

from .ai import policy as ai_policy
from .models import (
    ACCOUNT_KINDS,
    ASSET_CLASSES,
    DEFAULT_CLASS_BY_KIND,
    INCOME_CATEGORY,
    INTERNAL_CATEGORY,
    INTERNAL_TYPES,
    LIABILITY_CLASSES,
    LIABILITY_KINDS,
    MODULE_IDS,
    TXN_TYPES,
)
from .networth import MAX_MANUAL_ID_LEN, MAX_MANUAL_VALUE, manual_slug

# the config files Config reads; the first two are required, a missing optional one reads as empty
CONFIG_FILES = ("household.yaml", "accounts.yaml", "assumptions.yaml", "category_rules.yaml", "vendor_rules.yaml",
                "transfer_rules.yaml", "bank_category_map.yaml", "overrides.yaml")
REQUIRED_FILES = ("household.yaml", "accounts.yaml")
# the one file whose top level is a list; every other file is a mapping
LIST_FILES = ("overrides.yaml",)
# the folder of the config that holds the household's own statement layouts, one CSV preset a file (<id>.yaml): each is
# checked as a shipped preset is (etl/importers/csv_preset.py, whose keys docs/configuration.md lists under "Your own
# file layouts"), and its id is a format name of that household (config.household_presets)
PRESETS_DIR = "presets"
# the only `version` this release reads
VERSION = 1
# where a person setting up a household reads what every key means
DOCS = "docs/configuration.md"
# household.yaml `refresh`: the hours of the day, on the household's clock, the scheduled refresh runs at when the file
# names none, the minute past each, and how many hours it may name. The bank feed allows about 24 requests a day: six
# scheduled refreshes and the dashboard's manual ones (deploy/refresh_state.py MAX_MANUAL_PER_DAY) stay well under it.
# deploy/make_crontab.py writes the scheduler's crontab from these; deploy/refresh_state.py MAX_SCHEDULE_HOURS is the
# same bound (tests/test_make_crontab.py pins the two equal).
DEFAULT_REFRESH_HOURS = (1, 9, 17)
DEFAULT_REFRESH_MINUTE = 0
MAX_REFRESH_HOURS = 6
# household.yaml `ai`, the optional AI step (etl/ai/): who is asked, the model the anthropic provider asks when the file
# names none (openai has no default), whose rows are asked about, and each number's default and its range (inclusive)
AI_PROVIDERS = ("anthropic", "openai")
AI_DEFAULT_MODEL = "claude-opus-5-5"
AI_ACCOUNTS = ("cards", "all")
AI_NUMBERS = {                       # key path under `ai` -> (default, lowest, highest, whole number)
    "min_seen": (2, 1, 100, True),
    "confidence": (0.8, 0.5, 1, False),
    "caps.vendors_per_run": (50, 1, 200, True),
    "caps.runs_per_day": (4, 1, 24, True),
}
AI_MAX_MODEL_LENGTH = 100

_HOUSEHOLD = "household.yaml"
_ACCOUNTS = "accounts.yaml"


# ------------------------------------------------------------------------------------------------ the problems

# a key path at the head of a problem's text: no spaces and no colon, optionally followed by ` (<a rule's name>)`
_PATH = re.compile(r"(?P<path>[^\s:]+(?: \([^()]*\))?): ")


@dataclass(frozen=True)
class Problem:
    """One problem: the file, the key path in it (empty when the problem is the file's as a whole), what is wrong and
    how to fix it."""
    file: str
    path: str
    message: str
    fix: str

    def __str__(self) -> str:
        where = f"{self.file}: {self.path}: " if self.path else f"{self.file}: "
        return f"{where}{self.message} — {self.fix}" if self.fix else f"{where}{self.message}"

    @classmethod
    def parse(cls, text: str) -> Problem:
        """The problem written as `text` in the rendered form: the file is the text up to the first ': ', the fix the
        text after the last ' — ' (empty when there is none), and the key path the leading key-path-shaped token of
        what remains when ': ' follows it (else empty, and the message is all of it). str() gives `text` back."""
        file, sep, rest = text.partition(": ")
        if not sep:
            return cls("", "", text, "")
        body, dash, fix = rest.rpartition(" — ")
        if not dash:
            body, fix = rest, ""
        m = _PATH.match(body)
        if m:
            return cls(file, m["path"], body[m.end():], fix)
        return cls(file, "", body, fix)


def _order(p: Problem) -> tuple:
    """File, then key path with its numbers compared as numbers (members[2] before members[10]), then the text."""
    return (p.file, tuple(int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p.path)), p.message, p.fix)


class Problems:
    """The collector. `add` takes a Problem, text in the rendered form (Problem.parse) or the four fields; iterating
    gives every problem once, sorted by file, then key path, then message."""

    def __init__(self, items: Iterable[Problem | str] = ()):
        self._items: dict[Problem, None] = {}
        self.extend(items)

    def add(self, item: Problem | str, path: str | None = None, message: str | None = None,
            fix: str | None = None) -> None:
        if isinstance(item, Problem):
            problem = item
        elif path is None:
            problem = Problem.parse(item)
        else:
            problem = Problem(item, path, message or "", fix or "")
        self._items[problem] = None

    def extend(self, items: Iterable[Problem | str]) -> None:
        for item in items:
            self.add(item)

    def __bool__(self) -> bool:
        return bool(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[Problem]:
        return iter(sorted(self._items, key=_order))


class ConfigError(ValueError):
    """Every problem of a config: `problems` in the collector's order, and the message one rendered problem per
    line."""

    def __init__(self, problems: Iterable[Problem | str]):
        self.problems: list[Problem] = list(problems if isinstance(problems, Problems) else Problems(problems))
        super().__init__("\n".join(str(p) for p in self.problems))


# ------------------------------------------------------------------------------------------------ the registry

@dataclass(frozen=True)
class Key:
    """One key a validator knows: its file, its key path pattern, its type, its default (`required` when it has
    none) and what it means, in one line for docs/configuration.md."""
    file: str
    path: str
    type: str
    default: str
    meaning: str


def _keys(file: str, *rows: tuple[str, str, str, str]) -> tuple[Key, ...]:
    return tuple(Key(file, *row) for row in rows)


_CLASSES = "an asset class"
_VERSION_ROW = ("version", "whole number", "1", "The format of this file; this release reads version 1.")

KEYS: tuple[Key, ...] = (
    *_keys(
        _HOUSEHOLD,
        _VERSION_ROW,
        ("name", "text", "the surname, else Household", "The household's name, exported as `assumptions.json` "
                                                        "`household.name`."),
        ("surname", "text", "none", "The family name; with the adults' names it gives the words that mark a payment "
                                    "between the household's own accounts."),
        ("members", "list", "required", "The people of the household, one entry each; at least one is an adult."),
        ("members[].name", "text", "required", "The person's name, as `owner:` and `earner:` keys write it; no two "
                                               "members share one, compared ignoring case."),
        ("members[].role", "adult or child", "required", "Adults are listed first among the owners, then the joint "
                                                         "label, then the children."),
        ("members[].birth_year", "whole number", "none", "The year the person was born, from 1900 to 2100."),
        ("joint_label", "text", "required", "The owner name of what the household holds jointly, e.g. Joint; no "
                                            "member may have it."),
        ("timezone", "text", "required", "The IANA zone name, e.g. America/New_York, that decides which day every "
                                         "bank-feed time falls on, the hours the scheduled refresh runs at and when "
                                         "the day's refresh limits start again."),
        ("refresh", "mapping", "{}", "When the scheduled refresh runs, on the household's clock (`timezone`)."),
        ("refresh.hours", "list of whole numbers", f"[{', '.join(str(h) for h in DEFAULT_REFRESH_HOURS)}]",
         f"The hours of the day, from 0 to 23, the scheduled refresh runs at: one to {MAX_REFRESH_HOURS} of them, each "
         "once. The bank feed allows about 24 requests a day, which these and the dashboard's manual refreshes stay "
         "well under."),
        ("refresh.minute", "whole number", str(DEFAULT_REFRESH_MINUTE), "The minute past each of those hours, from 0 "
                                                                        "to 59."),
        ("modules", "mapping", "{}", "Which optional pages of the app are on, exported as `assumptions.json` "
                                     "`modules`; a module switched in the dashboard's Settings follows that switch "
                                     "instead, and Settings says which of the two is in force. The modules: "
                                     f"{', '.join(MODULE_IDS)}."),
        ("modules.<module>", "true or false", "none", "Switches one optional part on (true) or off (false) until the "
                                                      "dashboard's own switch for it says otherwise; an id that is no "
                                                      "module is refused."),
        ("net_worth", "mapping", "{}", "How net worth is counted."),
        ("net_worth.include_credit_cards", "true or false", "true", "Whether credit-card balances count as debts in "
                                                                    "net worth."),
        ("ai", "mapping", "{}", "The optional AI step that suggests a category for the vendors no rule places. It sends "
                                "nothing until the household switches it on in the dashboard after seeing exactly what "
                                "would be sent, and a key is stored."),
        ("ai.provider", " or ".join(AI_PROVIDERS), AI_PROVIDERS[0],
         "Who is asked: Anthropic, or OpenAI or a server that speaks its interface, a model of your own among them. "
         "Changing it turns the step off until the household agrees again."),
        ("ai.model", "text", f"{AI_DEFAULT_MODEL} with anthropic", "The model asked; required with openai, which has "
                                                                    "no default."),
        ("ai.base_url", "text", "none", "With openai only: the address of the server asked, an https:// address the "
                                        "step's containers can reach; plain http:// only to the container's own "
                                        "loopback (127.0.0.1, localhost or [::1]); without one, OpenAI's own. "
                                        "Changing its host turns the step off until the household agrees again."),
        ("ai.accounts", " or ".join(AI_ACCOUNTS), AI_ACCOUNTS[0], "Whose uncategorized purchases are asked about: the "
                                                                  "credit-card accounts' only, or every account's. "
                                                                  "Changing it turns the step off until the "
                                                                  "household agrees again."),
        ("ai.never_send", "list of text", "[]", "Words never sent, besides the household's names: nicknames, a pet, "
                                                "a street, a school, a landlord. Each is split into words as a name "
                                                "is, and no vendor or category holding one is sent."),
        ("ai.min_seen", "whole number", str(AI_NUMBERS["min_seen"][0]),
         "A vendor is asked about once at least this many of its purchases are uncategorized, from 1 to 100."),
        ("ai.confidence", "number", str(AI_NUMBERS["confidence"][0]),
         "An answer at least this sure, from 0.5 to 1, files the vendor's purchases; a less sure one is kept, not "
         "applied."),
        ("ai.caps", "mapping", "{}", "How much the AI step may ask."),
        ("ai.caps.vendors_per_run", "whole number", str(AI_NUMBERS["caps.vendors_per_run"][0]),
         "The most vendors one run asks about, from 1 to 200."),
        ("ai.caps.runs_per_day", "whole number", str(AI_NUMBERS["caps.runs_per_day"][0]),
         "The most runs in a day on the household's clock, from 1 to 24."),
    ),
    *_keys(
        _ACCOUNTS,
        _VERSION_ROW,
        ("source_dir", "text", "none", "The statements folder, relative to the folder that holds config/; needed when "
                                       "an account reads statement files and neither FF_IMPORTS_DIR nor FF_HOME is "
                                       "set."),
        ("skip_files", "list of text", "[]", "File-name patterns in the statements folder that are never read."),
        ("simplefin", "mapping", "{}", "Where the bank feed keeps what it fetched."),
        ("simplefin.store_dir", "text", "../simplefin", "The feed's snapshot store, relative to the folder that holds "
                                                        "config/ (FF_STORE_DIR or FF_HOME wins)."),
        ("simplefin.ignore", "list of text", "[]", "Feed account ids that no account maps, on purpose: they raise no "
                                                   "warning."),
        ("household", "mapping", "{}", "Who \"we\" are in payment descriptions."),
        ("household.own_name_tokens", "list of text", "the adults' names and the surname",
         "Words that mark a person-to-person payment as one between the household's own accounts."),
        ("accounts", "mapping", "required", "Every account, keyed by its id."),
        ("accounts.<id>", "mapping", "none", "One account. Its id is letters, digits, _ and -, and names it in rules "
                                             "and in the dashboard's addresses."),
        ("accounts.<id>.name", "text", "required", "The account's name, as the dashboard shows it; no two accounts "
                                                   "share one, compared ignoring case."),
        ("accounts.<id>.institution", "text", "required", "The bank or broker that holds it."),
        ("accounts.<id>.owner", "an owner", "required", "An adult's name, the joint label or a child's name from "
                                                        "household.yaml."),
        ("accounts.<id>.kind", "checking, savings, credit-card, brokerage or loan", "required",
         "What sort of account it is."),
        ("accounts.<id>.asset_class", _CLASSES, "by kind", "The class its balance counts under in net worth; a card "
                                                           "or a loan takes a debt class, other kinds do not."),
        ("accounts.<id>.tags", "list of text", "[]", "Tags every transaction of the account carries."),
        ("accounts.<id>.parser", "a statement format", "none", "The format its statement files are read with; "
                                                               "needs glob."),
        ("accounts.<id>.glob", "text", "none", "The file-name pattern of its statement files in the statements "
                                               "folder; needs parser."),
        ("accounts.<id>.sources", "list", "none", "Its statement files when they come in more than one format, such "
                                                  "as older exports in one and newer downloads in another: one entry "
                                                  "per source, oldest first, each holding its own days. Given instead "
                                                  "of parser and glob."),
        ("accounts.<id>.sources[].format", "a statement format", "required", "The format the source's files are "
                                                                             "read with; the names parser takes."),
        ("accounts.<id>.sources[].glob", "text", "required", "The file-name pattern of the source's files in the "
                                                             "statements folder; no two sources share one."),
        ("accounts.<id>.sources[].since", "date", "none", "The first day the source holds; the source before it "
                                                          "holds the days before. The first source has none; each "
                                                          "later one needs one, after the one before it and before "
                                                          "the account's simplefin_cutover."),
        ("accounts.<id>.simplefin_id", "text", "none", "The bank feed's id for the account; no two accounts share "
                                                       "one."),
        ("accounts.<id>.simplefin_id_history", "list of text", "[]", "Earlier feed ids of the same account, from a "
                                                                     "connection made again."),
        ("accounts.<id>.simplefin_cutover", "date", "none", "From this day on the feed owns the account's rows; "
                                                            "statement files own the days before. An account with "
                                                            "statement files and a feed id (current or earlier) needs "
                                                            "it, unless it reads balances only; one that reads "
                                                            "balances only never states it."),
        ("accounts.<id>.simplefin_balances_only", "true or false", "by kind", "Read only balances from the feed, no "
                                                                              "transactions; the default is true for "
                                                                              "a brokerage or loan with no statement "
                                                                              "files and no cutover. Never stated "
                                                                              "true with a cutover."),
        ("accounts.<id>.credit_limit", "number", "none", "A card's credit limit in dollars, used when the feed gives "
                                                         "none."),
        ("accounts.<id>.margin", "true or false", "false", "A brokerage whose negative cash is a margin loan."),
        ("accounts.<id>.mortgage", "true or false", "false", "The loan that is the home's mortgage; at most one "
                                                             "account."),
        ("accounts.<id>.loan", "mapping", "none", "A loan account's terms; every one is optional."),
        ("accounts.<id>.loan.rate", "number", "none", "The yearly interest rate in percent, e.g. 6.5."),
        ("accounts.<id>.loan.monthly_payment", "number", "none", "The monthly payment in dollars."),
        ("accounts.<id>.loan.monthly_escrow", "number", "none", "The part of the payment that goes to escrow, in "
                                                                "dollars."),
        ("accounts.<id>.loan.escrow_balance", "number", "none", "The escrow account's balance in dollars."),
        ("accounts.<id>.loan.term_months", "whole number", "none", "The loan's length in months, e.g. 360."),
        ("accounts.<id>.loan.origination", "mapping", "none", "When the loan was made and for how much."),
        ("accounts.<id>.loan.origination.date", "date", "none", "The day the loan was made."),
        ("accounts.<id>.loan.origination.amount", "number", "none", "The dollars borrowed."),
        ("accounts.<id>.note", "text", "none", "Your own note; the engine does not read it."),
        ("manual_entries", "list", "[]", "Values no feed or statement reports, such as a home, a car or a debt "
                                         "(stated negative), each counted from its `acquired` day when it states one, "
                                         "else carried back before its first value. The dashboard's Settings → Manual "
                                         "assets enters more, and replaces an entry with an asset of the entry's id "
                                         "(its name as lowercase letters and digits, runs of anything else become one "
                                         "dash, none at either end) until that asset is removed there; it never "
                                         "removes an entry."),
        ("manual_entries[].account", "text", "required", "The entry's name; no two entries share one, and none is an "
                                                         "account's name, compared ignoring case. It holds a letter or "
                                                         "digit, and its id is at most 64 characters."),
        ("manual_entries[].institution", "text", "required", "Who holds or values it."),
        ("manual_entries[].owner", "an owner", "required", "An adult's name, the joint label or a child's name."),
        ("manual_entries[].asset_class", _CLASSES, "required", "The class the value counts under; House for the "
                                                               "home."),
        ("manual_entries[].value", "number", "required", "The value in dollars, from -1e12 to 1e12; negative for a "
                                                         "debt."),
        ("manual_entries[].as_of", "date", "required", "The day the value was true; before it, net worth counts the "
                                                       "same value."),
        ("manual_entries[].acquired", "date", "none", "The day it was bought, on or before as_of; before it, net worth "
                                                      "does not count it."),
        ("manual_entries[].liquid", "true or false", "false", "Whether it counts as money you could spend soon; a "
                                                              "debt never does."),
        ("manual_entries[].note", "text", "none", "Your own note; the engine does not read it."),
    ),
    *_keys(
        "assumptions.yaml",
        _VERSION_ROW,
        ("match_window_bdays", "whole number", "3", "Business days apart the two legs of a transfer may be."),
        ("venmo_match_window_bdays", "whole number", "5", "The same window for payment-app transfers."),
        ("venmo_review_threshold", "number", "200", "Payment-app amounts in dollars above which an unexplained one is "
                                                    "sent to review."),
        ("uncategorized_review_threshold", "number", "250", "Uncategorized purchases in dollars above which one is "
                                                            "sent to review."),
        ("unknown_credit_review_threshold", "number", "500", "Unexplained money in, in dollars, above which it is "
                                                             "sent to review."),
        ("generic_min_amount", "number", "20.00", "The smallest amount in dollars the matcher pairs as a transfer "
                                                  "when no rule explains it."),
        ("coverage", "mapping", "{}", "From which month rates and averages count."),
        ("coverage.min_share", "number", "0.6", "Rates and averages start at the first month by which at least this "
                                                "share of your everyday accounts that have any transaction have had "
                                                "their first one (above 0, at most 1)."),
        ("cancelled_subscriptions", "list", "[]", "Subscriptions you ended, so the forecast stops expecting them."),
        ("cancelled_subscriptions[]", "mapping", "none", "One subscription you ended."),
        ("cancelled_subscriptions[].vendor", "text", "required", "Its vendor as the Recurring page names it, compared "
                                                                 "ignoring case."),
        ("cancelled_subscriptions[].recorded_on", "date", "none", "The day you wrote it down; the engine does not "
                                                                  "read it."),
        ("cancelled_subscriptions[].permanent", "true or false", "none", "Whether you mean it for good; the engine "
                                                                         "does not read it."),
        ("cancelled_subscriptions[].note", "text", "none", "Your own note; the engine does not read it."),
        ("household", "mapping", "{}", "Household facts the projections use."),
        ("household.margin_apr", "a rate from 0 up to 1", "none: margin interest is not estimated",
         "The margin loan's yearly rate as a fraction, e.g. 0.07 for 7 %."),
        ("household.retirement_accounts", "mapping", "{}", "Retirement account name -> the owner it belongs to."),
        ("household.retirement_accounts.<name>", "an owner", "none", "One retirement account: its name (an "
                                                                     "account's name or a manual entry's account) "
                                                                     "and its owner."),
        ("projections", "mapping", "{}", "The projection defaults the dashboard starts from."),
        ("projections.return_scenarios_pct", "list of one or more numbers", "[5, 7, 9]", "The yearly returns, in "
                                                                                         "percent, the projections "
                                                                                         "compare; written, it holds "
                                                                                         "at least one number (an "
                                                                                         "empty list is refused)."),
        ("projections.inflation_pct", "number", "3.0", "Yearly inflation in percent."),
        ("projections.withdrawal_rate_pct", "number", "4.0", "The yearly withdrawal rate in retirement, in percent."),
        ("projections.ltcg_pct", "number", "15.0", "The long-term capital gains tax rate in percent."),
        ("projections.state_income_tax_pct", "number", "0.0", "The state income tax rate in percent; set your "
                                                              "state's rate."),
        ("targets", "mapping", "{}", "The target asset mix: asset class -> percent of net worth; the percents need "
                                     "not add up to 100."),
        ("targets.<class>", "number", "none", "One asset class (no debt class) and its target, from 0 to 100."),
        ("budgets", "mapping", "{}", "Monthly budgets by category."),
        ("budgets.<category>", "number", "none", "One category of the taxonomy and its monthly budget in dollars, "
                                                 "0 or more."),
    ),
    *_keys(
        "category_rules.yaml",
        _VERSION_ROW,
        ("taxonomy", "mapping", "the reserved names", "Every category and its subcategories."),
        ("taxonomy.<category>", "list of text", "[]", "One category and its subcategories (it may have none)."),
        ("keyword_rules", "list", "[]", "Rules that file a transaction under a category by its description or other "
                                        "fields."),
        ("keyword_rules[]", "mapping", "none", "One rule, in the legacy shape (pattern and the category it files) or "
                                               "the structured one (all or any, and set)."),
        ("keyword_rules[].pattern", "a regular expression", "none", "Legacy shape: matched, case ignored, against the "
                                                                    "cleaned description or the raw one."),
        ("keyword_rules[].category", "text", "none", "Legacy shape: the category a matching row is filed under."),
        ("keyword_rules[].subcategory", "text", "none", "Legacy shape: its subcategory, listed under it in the "
                                                        "taxonomy."),
        ("keyword_rules[].tags", "list of text", "[]", "Legacy shape: tags a matching row carries."),
        ("keyword_rules[].needs_review", "true or false", "false", "Legacy shape: send a row it files to review from "
                                                                   "uncategorized_review_threshold up."),
        ("keyword_rules[].name", "text", "keyword_rules:<index>", "The rule's name; no two rules share one."),
        ("keyword_rules[].stage", "pre, default or post", "default", "When it runs: every pre rule, then default, "
                                                                     "then post, file order within a stage."),
        ("keyword_rules[].all", "list", "none", "Structured shape: conditions that must all hold; give all or any."),
        ("keyword_rules[].all[]", "mapping", "none", "One condition."),
        ("keyword_rules[].all[].field", "description, vendor, amount, account, owner, type or bank_category",
         "required", "What the condition reads."),
        ("keyword_rules[].all[].op", "an operator", "required", "How it compares: is, isNot, oneOf, contains, matches "
                                                               "(text); gt, gte, lt, lte, between, inflow, outflow "
                                                               "(amount)."),
        ("keyword_rules[].all[].value", "text, a number or a list", "none", "What it compares with: dollars for an "
                                                                            "amount, negative for money out; inflow "
                                                                            "and outflow take none."),
        ("keyword_rules[].any", "list", "none", "Structured shape: conditions of which one must hold."),
        ("keyword_rules[].any[]", "mapping", "none", "One condition."),
        ("keyword_rules[].any[].field", "description, vendor, amount, account, owner, type or bank_category",
         "required", "What the condition reads."),
        ("keyword_rules[].any[].op", "an operator", "required", "How it compares, as under all."),
        ("keyword_rules[].any[].value", "text, a number or a list", "none", "What it compares with, as under all."),
        ("keyword_rules[].set", "mapping", "none", "Structured shape: what a matching row is given."),
        ("keyword_rules[].set.category", "text", "required", "The category it is filed under."),
        ("keyword_rules[].set.subcategory", "text", "none", "Its subcategory."),
        ("keyword_rules[].set.tags", "list of text", "[]", "Tags it carries."),
        ("keyword_rules[].set.needs_review", "true or false", "false", "Send it to review from "
                                                                       "uncategorized_review_threshold up."),
        ("keyword_rules[].note", "text", "none", "Your own note; the engine does not read it."),
        ("income_rules", "list", "[]", "Rules that recognise income and its earner."),
        ("income_rules[]", "mapping", "none", "One rule: deposits into a checking or savings account whose "
                                              "description its pattern finds are income."),
        ("income_rules[].pattern", "a regular expression", "required", "Matched, case ignored, against the deposit's "
                                                                       "description."),
        ("income_rules[].accounts", "account ids", "every checking and savings account", "The accounts it reads."),
        ("income_rules[].type", "income, dividend or interest", "income", "The type a matching deposit takes."),
        ("income_rules[].subcategory", "text", "none", "Its subcategory, listed under the income category in the "
                                                       "taxonomy."),
        ("income_rules[].earner", "a member", "from the subcategory", "Who earned it: a member of household.yaml, "
                                                                      "never the joint label."),
        ("income_rules[].employer", "text", "none", "Who paid it; one job per earner and employer is drawn on the "
                                                    "cash-flow chart."),
        ("income_rules[].note", "text", "none", "Your own note; the engine does not read it."),
        ("renames", "list", "[]", "Category renames; stored references follow them."),
        ("renames[]", "mapping", "none", "One rename: every stored reference to from is read as to."),
        ("renames[].from", "mapping", "required", "The old name."),
        ("renames[].from.category", "text", "required", "Its category (not a reserved one)."),
        ("renames[].from.subcategory", "text", "none", "Its subcategory; without the key the whole category moves, and "
                                                       "with no value (`subcategory:` left blank) it names the "
                                                       "category's rows that have no subcategory."),
        ("renames[].to", "mapping", "required", "The new name, already in the taxonomy."),
        ("renames[].to.category", "text", "required", "Its category."),
        ("renames[].to.subcategory", "text", "none", "Its subcategory."),
    ),
    *_keys(
        "vendor_rules.yaml",
        _VERSION_ROW,
        ("strip_prefixes", "list of text", "[]", "Patterns cut from the start of a description before it is read."),
        ("cleanup", "list of text", "[]", "Patterns removed from a description, such as store numbers."),
        ("acronyms", "list of text", "[]", "Words kept upper-case in a vendor name."),
        ("patterns", "list", "[]", "Vendor patterns: a description pattern and the vendor (and category) it names."),
        ("patterns[]", "mapping", "none", "One pattern; the first that matches names the vendor."),
        ("patterns[].pattern", "a regular expression", "required", "Matched, case ignored, against the cleaned "
                                                                   "description (the raw one when cleaning leaves "
                                                                   "nothing)."),
        ("patterns[].vendor", "text", "required", "The vendor's name."),
        ("patterns[].category", "text", "none", "The category the vendor's purchases are filed under; it outranks "
                                                "keyword rules."),
        ("patterns[].subcategory", "text", "none", "Its subcategory."),
        ("patterns[].tags", "list of text", "[]", "Tags the vendor's rows carry."),
        ("patterns[].learn", "true or false", "true", "Whether review answers may teach the vendor a category."),
        ("patterns[].split", "list", "none", "Legs that split each purchase into parts (two or more, one the "
                                             "remainder)."),
        ("patterns[].split[]", "mapping", "none", "One leg."),
        ("patterns[].split[].pct", "number", "none", "Its share of the purchase in percent (above 0, below 100)."),
        ("patterns[].split[].amount", "number", "none", "Its fixed amount in dollars."),
        ("patterns[].split[].remainder", "true", "none", "The leg that takes what the others leave; exactly one."),
        ("patterns[].split[].category", "text", "required", "The category of the part."),
        ("patterns[].split[].subcategory", "text", "none", "Its subcategory."),
        ("patterns[].note", "text", "none", "Your own note; the engine does not read it."),
        ("learning", "mapping", "{}", "How vendor rules are learned from review answers."),
        ("learning.exclude", "list of text", "[]", "Vendors review answers never teach a category, e.g. a shop that "
                                                   "sells everything."),
        ("renames", "list", "[]", "Vendor renames; stored references follow them."),
        ("renames[]", "mapping", "none", "One rename: every stored reference to from is read as to."),
        ("renames[].from", "text", "required", "The old vendor name, compared ignoring case; not a pattern's vendor."),
        ("renames[].to", "text", "required", "The new name."),
    ),
    *_keys(
        "transfer_rules.yaml",
        _VERSION_ROW,
        ("rules", "list", "[]", "Rules that recognise a transfer between the household's own accounts."),
        ("rules[]", "mapping", "none", "One rule; the first that matches a purchase row types it."),
        ("rules[].name", "text", "rules[<index>]", "The rule's name, as problems and review questions write it."),
        ("rules[].pattern", "a regular expression", "required", "Matched, case ignored, against the description."),
        ("rules[].type", "transfer, cc-payment or investment", "required", "The type a matching row takes."),
        ("rules[].accounts", "account ids", "none", "The accounts it reads; give accounts or kinds."),
        ("rules[].kinds", "account kinds", "none", "The kinds of account it reads; give accounts or kinds."),
        ("rules[].sign", "credit or debit", "both", "Only money in (credit) or only money out (debit)."),
        ("rules[].bank_type", "text", "none", "Only rows the bank gave this type code."),
        ("rules[].counter_hint", "a counter hint", "none", "Where the other leg lives: an account id, a group, "
                                                          "kind:<kind>, bank_any, an outside name or "
                                                          "from_text:<template>."),
        ("rules[].needs_review", "true or false", "false", "Send a row it types to review."),
        ("rules[].open_question", "text", "none", "A question for the family, asked once, with the first row it "
                                                  "types."),
        ("rules[].note", "text", "none", "Your own note; the engine does not read it."),
        ("groups", "mapping", "{}", "Named groups of accounts a rule's counter_hint can point at."),
        ("groups.<name>", "mapping", "none", "One group, by a name no account or outside name has."),
        ("groups.<name>.accounts", "account ids", "required", "Its accounts."),
        ("groups.<name>.expect_counterpart", "true or false", "required", "Whether a leg left unmatched while a member "
                                                                          "has data for its date is a data gap."),
        ("outside", "list of text", "[]", "Names of places outside the household a counter_hint can point at."),
        ("venmo_patterns", "mapping", "{}", "The patterns that recognise payment-app and Zelle rows."),
        ("venmo_patterns.debit", "a regular expression", "none", "A payment-app payment out."),
        ("venmo_patterns.credit", "a regular expression", "none", "A payment-app cash-out in."),
        ("venmo_patterns.zelle", "a regular expression", "none", "A Zelle row."),
        ("venmo_patterns.zelle_party", "a regular expression", "none", "Its wording that names the counterparty, in "
                                                                       "exactly one group."),
        ("venmo_patterns.zelle_bank_types", "list of text", "[]", "Bank type codes that mark a Zelle row."),
        ("venmo_patterns.cashout_income", "list", "[]", "Where an unmatched cash-out is filed, by account."),
        ("venmo_patterns.cashout_income[]", "mapping", "none", "One group of accounts."),
        ("venmo_patterns.cashout_income[].accounts", "account ids", "required", "Its accounts."),
        ("venmo_patterns.cashout_income[].subcategory", "text", "required", "The income subcategory its cash-outs "
                                                                             "take."),
        ("venmo_patterns.cashout_income[].earner", "a member", "none", "Who earned them."),
        ("venmo_patterns.pairing", "unique or nearest", "unique", "`unique` pairs a payment-app or Zelle row that no "
                                                                  "transfer rule typed with a row on another of your "
                                                                  "accounts only when neither could have another "
                                                                  "counterpart; `nearest` takes the best-hinted, then "
                                                                  "closest, match."),
        ("generic_matching", "true or false", "true", "Whether exactly opposite rows that no transfer rule typed, no "
                                                      "category or learned rule filed and the family has not answered "
                                                      "are paired as transfers."),
    ),
    *_keys(
        "bank_category_map.yaml",
        _VERSION_ROW,
        ("<label>", "mapping", "none", "One bank's own category label and where its rows go."),
        ("<label>.category", "text", "required", "The category a row with that label is filed under."),
        ("<label>.subcategory", "text", "none", "The subcategory."),
        ("<label>.tags", "list of text", "[]", "Tags the row carries."),
    ),
    *_keys(
        "overrides.yaml",
        ("[]", "mapping", "none", "One override: which transactions it matches and what it sets."),
        ("[].match", "mapping", "required", "What a transaction must have to match: one or more of the keys below."),
        ("[].match.uid", "text", "none", "The transaction's id."),
        ("[].match.vendor", "text", "none", "Its vendor, compared ignoring case."),
        ("[].match.account", "an account id", "none", "Its account."),
        ("[].match.date", "date", "none", "Its date."),
        ("[].match.amount", "number", "none", "Its amount in dollars, negative for money out."),
        ("[].match.contains", "text", "none", "Words in its description, compared ignoring case."),
        ("[].set", "mapping", "required", "What a matched transaction is given."),
        ("[].set.type", "a transaction type", "none", "Its type: purchase, income, transfer, cc-payment, investment, "
                                                      "dividend, fee or interest."),
        ("[].set.category", "text", "none", "Its category, in the taxonomy."),
        ("[].set.subcategory", "text", "none", "Its subcategory, listed under the category (null clears it)."),
        ("[].set.tags", "list of text", "none", "Tags added to it."),
        ("[].set.needs_review", "true or false", "none", "Send it to review, or take it out."),
        ("[].set.earner", "a member", "none", "Who earned it: a member of household.yaml (left blank, it clears the "
                                              "row's earner)."),
        ("[].note", "text", "none", "Your own note; the engine repeats it in this override's warning when it matches "
                                    "nothing."),
    ),
)

# keys refused by a check of their own (each says where its value went or why the key does not belong), so no
# unknown-key check reports them
REFUSED_ELSEWHERE = frozenset({
    ("category_rules.yaml", "keyword_rules[].vendor"),
    ("category_rules.yaml", "keyword_rules[].split"),
    ("category_rules.yaml", "keyword_rules[].set.vendor"),
    ("vendor_rules.yaml", "patterns[].stage"),
    ("vendor_rules.yaml", "patterns[].name"),
})


def _split(path: str) -> tuple[str, str]:
    """A key path pattern's parent pattern and last key: `members[].name` -> (`members[]`, `name`)."""
    parent, _, name = path.rpartition(".")
    return parent, name


def children(file: str, pattern: str) -> list[str]:
    """The keys the registry knows directly under the block at `pattern` in `file` (`` for the top level), in
    registry order; a chosen name (`<id>`) and a list's item (`rules[]`) are none of them."""
    return [name for k in KEYS if k.file == file
            for parent, name in (_split(k.path),)
            if parent == pattern and not name.startswith("<") and not name.endswith("[]")]


def _join(where: str, key) -> str:
    return f"{where}.{key}" if where else str(key)


def unknown_key_problems(file: str, pattern: str, where: str, block: Mapping) -> list[Problem]:
    """A problem for each key of `block` — the block at key path `where`, matching the registry pattern `pattern` —
    that the registry does not know there, with the nearest known key as the fix when one is close."""
    known = children(file, pattern)
    out = []
    for key in block:
        if key in known or (file, _join(pattern, key)) in REFUSED_ELSEWHERE:
            continue
        near = difflib.get_close_matches(str(key), known, n=1)
        fix = (f"did you mean '{near[0]}'?" if near
               else f"remove it, or see {DOCS} for the keys this file takes")
        out.append(Problem(file, _join(where, key), "unknown key", fix))
    return out


# ------------------------------------------------------------------------------------------------ value tests

def is_text(v) -> bool:
    return isinstance(v, str) and v.strip() != ""


def encodes_as_utf8(doc) -> bool:
    """Every string of a parsed JSON document, keys included, is text UTF-8 can carry: none holds a lone surrogate,
    which JSON can spell ("\\ud800") but the database refuses, so a family document holding one is read as corrupt
    rather than stopping the ingest where its text is written. Walked without recursion. deploy/scenario_store.py
    encodes_as_utf8 is the same rule."""
    stack = [doc]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            try:
                node.encode("utf-8")
            except UnicodeEncodeError:
                return False
        elif isinstance(node, dict):
            stack.extend(node.keys())
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return True


def is_number(v) -> bool:
    """A finite int or float the engine can use as a float: YAML's .inf and .nan are no amounts, booleans are no
    numbers, and neither is a whole number too large for a float (its conversion overflows wherever it is used)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    try:
        return math.isfinite(float(v))
    except OverflowError:
        return False


_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def iso_date(v) -> str | None:
    """A YAML date, or text written YYYY-MM-DD that names a real day, as its ISO text; None for anything else (a
    YAML timestamp with a time of day included)."""
    if isinstance(v, dt.datetime):
        return None
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, str) and _ISO_DAY.fullmatch(v):
        try:
            return dt.date.fromisoformat(v).isoformat()
        except ValueError:
            return None
    return None


def _is_list_of_text(v) -> bool:
    return isinstance(v, list) and all(is_text(x) for x in v)


# registry type -> the test a value of that type passes; a type outside this table is checked by its own check
_TYPE_TESTS = {
    "mapping": lambda v: isinstance(v, dict),
    "list": lambda v: isinstance(v, list),
    "list of text": _is_list_of_text,
    "text": is_text,
    "number": is_number,
    "whole number": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "true or false": lambda v: isinstance(v, bool),
    "date": lambda v: iso_date(v) is not None,
}
_TYPE_WRITE = {
    "mapping": "`key: value` lines indented under it",
    "list": "one `- ...` line per item",
    "list of text": "a list of text such as [A, B]",
    "text": "text",
    "number": "a number",
    "whole number": "a whole number",
    "true or false": "true or false",
    "date": "the day as YYYY-MM-DD, e.g. 2030-01-31",
}
_ARTICLE = {"mapping": "a mapping", "list": "a list", "list of text": "a list of text", "text": "text",
            "number": "a number", "whole number": "a whole number", "true or false": "true or false",
            "date": "an ISO date"}


def _found(v) -> str:
    """What a value is, in words, for a fix that says what was found."""
    if v is None:
        return "nothing"
    if isinstance(v, bool):
        return "true or false"
    if isinstance(v, (int, float)):
        return "a number"
    if isinstance(v, str):
        return "empty text" if not v.strip() else repr(v)
    if isinstance(v, dict):
        return "a mapping"
    if isinstance(v, list):
        return "a list"
    return "a date" if isinstance(v, dt.date) else type(v).__name__


# ------------------------------------------------------------------------------------------------ the files

def missing_config_dir_problem(config_dir: Path, named_by: str = "default") -> Problem:
    """The one problem of a config directory that does not exist, wherever it was looked for (etl/config.py Paths):
    where that was, and advice that matches what named the place (`Paths.config_from`): the argument, FF_CONFIG_DIR
    or FF_HOME is to be corrected; the default place gives the two variables that point elsewhere."""
    if named_by == "argument":
        return Problem(str(config_dir), "", "the config directory given is not a directory",
                       "give one that holds household.yaml and accounts.yaml")
    if named_by == "FF_CONFIG_DIR":
        return Problem(str(config_dir), "", "FF_CONFIG_DIR names no directory", "correct FF_CONFIG_DIR or unset it")
    if named_by == "FF_HOME":
        return Problem(str(config_dir), "", "FF_HOME names a folder with no config/ in it",
                       "correct FF_HOME or unset it")
    return Problem(str(config_dir), "", "no config directory here",
                   "set FF_HOME to your household's folder (the one that holds config/) or FF_CONFIG_DIR to its "
                   "config folder; examples/minimal is the smallest household that runs")


def missing_file_problem(file: str, config_dir: Path) -> Problem:
    if file == _HOUSEHOLD:
        return Problem(file, "", f"not found in {config_dir}", "create it with version: 1, members (each a name and "
                                                               "role: adult|child), joint_label and timezone")
    return Problem(file, "", f"not found in {config_dir}", "create it with an accounts: block, each account's id "
                                                           "with its name, institution, owner and kind")


def yaml_problem(file: str, error: Exception) -> Problem:
    """The parser's own account of where the file stops being YAML: line and column, and what it found there."""
    mark = getattr(error, "problem_mark", None)
    what = getattr(error, "problem", None) or "the parser could not read it"
    where = f"line {mark.line + 1}, column {mark.column + 1}: {what}" if mark is not None else what
    return Problem(file, "", f"not valid YAML ({where})", "fix the syntax near that line")


def _first_unbuildable(text: str) -> yaml.ScalarNode | None:
    """The first value, in document order, that the YAML parser reads but cannot build (an unquoted date or time that
    does not exist, such as 2030-02-30), or None when the text does not parse or no single value is to blame."""
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        return None
    stack, seen = [root], set()
    while stack:
        node = stack.pop()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, yaml.ScalarNode):
            try:
                SafeConstructor().construct_object(node)
            except (ValueError, OverflowError):
                return node
            except yaml.YAMLError:
                continue
        elif isinstance(node, yaml.MappingNode):
            stack.extend(n for pair in reversed(node.value) for n in reversed(pair))
        elif isinstance(node, yaml.SequenceNode):
            stack.extend(reversed(node.value))
    return None


def unbuildable_value_problem(file: str, path: Path, error: Exception) -> Problem:
    """A file (at `path`) the YAML parser reads but cannot build a value of: an unquoted date or time that does not
    exist (the parser raises ValueError for 2030-02-30, a 13th month or an hour 25). The problem names the first such
    value with its line and column, and the parser's reason."""
    reason = str(error) or type(error).__name__
    try:
        with open(path) as f:
            node = _first_unbuildable(f.read())
    except (OSError, UnicodeDecodeError):
        node = None
    if node is None:
        where = reason
    else:
        mark = node.start_mark
        where = f"line {mark.line + 1}, column {mark.column + 1}: {node.value!r} cannot be read ({reason})"
    return Problem(file, "", f"not valid YAML ({where})", "write a valid value, or quote it to keep it as text")


def unreadable_problem(file: str, error: Exception) -> Problem:
    reason = getattr(error, "strerror", None) or type(error).__name__
    return Problem(file, "", f"could not be read ({reason})", "make it a readable text file")


def version_problems(file: str, doc: Mapping) -> list[Problem]:
    """An optional top-level `version`: absent (or null) or 1."""
    v = doc.get("version")
    if v is None or (v == VERSION and not isinstance(v, bool)):
        return []
    return [Problem(file, "version", f"unsupported version {v!r}", f"this release reads version {VERSION}")]


# the top-level blocks of a rule file whose type a check of their own (Config's, the transfer rules') reports in its
# own words
_TYPED_ELSEWHERE = frozenset({
    ("assumptions.yaml", "coverage"), ("assumptions.yaml", "generic_min_amount"),
    ("category_rules.yaml", "taxonomy"), ("vendor_rules.yaml", "learning"),
    *(("transfer_rules.yaml", k) for k in ("rules", "groups", "outside", "venmo_patterns", "generic_matching")),
})


def _top_key(file: str, key) -> Key | None:
    """The registry entry for a top-level key: its own, else the file's chosen-name entry (`<label>`), else None."""
    own = next((k for k in KEYS if k.file == file and k.path == key), None)
    if own is not None:
        return own
    return next((k for k in KEYS if k.file == file and k.path.startswith("<") and "." not in k.path), None)


def rule_file_problems(file: str, doc) -> tuple[list[Problem], object]:
    """A rule file's top level (every file but household.yaml and accounts.yaml): a mapping (overrides.yaml: a
    list; transfer_rules.yaml's own check reports one that is not), an optional `version` of 1, and each block the
    registry knows of its registry type. Returns the problems and the document the other checks read — the mapping
    without its null blocks (a null block reads as absent) and without a block of the wrong type — or None when the
    top level itself is wrong (or the file did not load: `doc` None)."""
    if doc is None:
        return [], None
    if file in LIST_FILES:
        if isinstance(doc, list):
            return [], doc
        return [Problem(file, "", "top level: expected a list",
                        "write one `- {match: ..., set: ...}` entry per override")], None
    if not isinstance(doc, dict):
        if file == "transfer_rules.yaml":
            return [], None
        return [Problem(file, "", "top level: expected a mapping",
                        f"write the file as `key: value` lines ({DOCS} lists its keys)")], None
    out = version_problems(file, doc)
    usable = {}
    for key, value in doc.items():
        if value is None:
            continue
        known = _top_key(file, key) if key != "version" else None
        test = _TYPE_TESTS.get(known.type) if known is not None else None
        if test is not None and (file, key) not in _TYPED_ELSEWHERE and not test(value):
            default = "" if known.default in ("none", "required", "{}", "[]") else f" (the default is {known.default})"
            out.append(Problem(file, str(key), f"expected {_ARTICLE[known.type]}",
                               f"write {_TYPE_WRITE[known.type]}, or leave the key out{default}"))
            continue
        usable[key] = value
    return out, usable


# ------------------------------------------------------------------------------------------------ household.yaml

def timezone_problems(doc: Mapping) -> list[Problem]:
    """household.yaml `timezone`: an IANA zone name, as text, that the zone database knows. It decides every day
    boundary the ETL draws, so an unknown name is refused rather than read as UTC, and `localtime` — whatever zone
    the machine happens to be set to — is refused like a value that is not text."""
    tz = doc.get("timezone")
    if tz is None:
        return [Problem(_HOUSEHOLD, "timezone", "missing", "set the household's IANA zone name, e.g. America/New_York")]
    expected = "expected an IANA zone name such as America/New_York"
    if not is_text(tz):
        return [Problem(_HOUSEHOLD, "timezone", expected, f"found {_found(tz)}; write the zone's name as text")]
    if tz == "localtime":
        return [Problem(_HOUSEHOLD, "timezone", expected, "found 'localtime', which is whatever zone the machine is "
                                                          "set to; write the household's own zone")]
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return [Problem(_HOUSEHOLD, "timezone", f"unknown zone {tz!r}", "use an IANA name such as America/New_York")]
    return []


def _whole(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def refresh_problems(doc: Mapping) -> list[Problem]:
    """household.yaml `refresh`, the scheduled refresh's times on the household's clock: absent (or null) for the
    defaults, else a mapping of the keys KEYS knows under it, `hours` a list of one to MAX_REFRESH_HOURS distinct whole
    hours from 0 to 23 and `minute` a whole minute from 0 to 59 (each left out, or null, for its default)."""
    block = doc.get("refresh")
    if block is None:
        return []
    example = "`refresh: {hours: [1, 9, 17], minute: 0}`"
    if not isinstance(block, dict):
        return [Problem(_HOUSEHOLD, "refresh", "expected a mapping", f"write {example}, or leave it out")]
    out = unknown_key_problems(_HOUSEHOLD, "refresh", "refresh", block)
    hours = block.get("hours")
    if hours is not None:
        if not isinstance(hours, list):
            out.append(Problem(_HOUSEHOLD, "refresh.hours", "expected a list of hours",
                               f"found {_found(hours)}; write them as a list such as [1, 9, 17]"))
        elif not 1 <= len(hours) <= MAX_REFRESH_HOURS:
            out.append(Problem(_HOUSEHOLD, "refresh.hours", f"{len(hours)} hours listed",
                               f"list one to {MAX_REFRESH_HOURS} hours: the bank feed allows about 24 requests a day"))
        seen: set[int] = set()
        for i, h in enumerate(hours if isinstance(hours, list) else ()):
            if not _whole(h) or not 0 <= h <= 23:
                out.append(Problem(_HOUSEHOLD, f"refresh.hours[{i}]", "expected a whole hour from 0 to 23",
                                   f"found {_found(h)}; write an hour of the day such as 9 or 17"))
            elif h in seen:
                out.append(Problem(_HOUSEHOLD, f"refresh.hours[{i}]", f"{h} is listed twice", "list each hour once"))
            else:
                seen.add(h)
    minute = block.get("minute")
    if minute is not None and (not _whole(minute) or not 0 <= minute <= 59):
        out.append(Problem(_HOUSEHOLD, "refresh.minute", "expected a whole minute from 0 to 59",
                           f"found {_found(minute)}; write a minute such as 0 or 30, or leave it out "
                           f"({DEFAULT_REFRESH_MINUTE})"))
    return out


_LOOPBACK_NAMES = frozenset({"localhost"})


def _loopback(host: str | None) -> bool:
    """Whether `host` (a URL's host name, brackets removed) names this computer."""
    if not host:
        return False
    if host.casefold() in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def base_url_problem(url: str) -> str | None:
    """What is wrong with an `ai.base_url`, or None: it must be an https:// address with a host, or an http:// one
    whose host is this computer (a local model), and it may hold no user name or password (the key has its own
    file)."""
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
        port_ok = parts.port is None or parts.port > 0
    except ValueError:
        return "not an address"
    if parts.username is not None or parts.password is not None:
        return "the address holds a user name or password"
    if parts.query or parts.fragment or "?" in url or "#" in url:
        return "the address holds a query or a fragment"
    if not host or not port_ok:
        return "not an address"
    if parts.scheme == "https" or (parts.scheme == "http" and _loopback(host)):
        return None
    return "the address is neither https:// nor http:// to this computer"


def ai_problems(doc: Mapping) -> list[Problem]:
    """household.yaml `ai`: absent (or null) for the defaults, else a mapping of the keys KEYS knows under it and under
    `ai.caps`; `provider` one of AI_PROVIDERS; `model` text without spaces, at most AI_MAX_MODEL_LENGTH characters, and
    required with openai; `base_url` with openai only, an https:// address or an http:// one to this computer, holding
    no user name or password; `accounts` one of AI_ACCOUNTS; each number of AI_NUMBERS in its range. A key written
    with no value reads as left out."""
    block = doc.get("ai")
    if block is None:
        return []
    if not isinstance(block, dict):
        return [Problem(_HOUSEHOLD, "ai", "expected a mapping", "write `ai: {provider: anthropic}`, or leave it out")]
    out = unknown_key_problems(_HOUSEHOLD, "ai", "ai", block)
    caps = block.get("caps")
    if caps is not None and not isinstance(caps, dict):
        out.append(Problem(_HOUSEHOLD, "ai.caps", "expected a mapping",
                           "write `caps: {vendors_per_run: 50, runs_per_day: 4}`, or leave it out"))
    elif isinstance(caps, dict):
        out += unknown_key_problems(_HOUSEHOLD, "ai.caps", "ai.caps", caps)
    provider = block.get("provider")
    known = provider is None or (isinstance(provider, str) and provider in AI_PROVIDERS)
    if not known:
        out.append(Problem(_HOUSEHOLD, "ai.provider", f"expected {' or '.join(AI_PROVIDERS)}",
                           f"found {_found(provider)}; write one of them, or leave it out ({AI_PROVIDERS[0]})"))
    provider = provider if known and provider is not None else AI_PROVIDERS[0]
    model = block.get("model")
    if model is None:
        if known and provider != "anthropic":
            out.append(Problem(_HOUSEHOLD, "ai.model", "missing",
                               f"name the model the {provider} server serves: it has no default"))
    elif not is_text(model) or any(c.isspace() for c in model) or len(model) > AI_MAX_MODEL_LENGTH:
        out.append(Problem(_HOUSEHOLD, "ai.model", "expected a model's name",
                           f"found {_found(model)}; write its name as text without spaces, at most "
                           f"{AI_MAX_MODEL_LENGTH} characters"))
    base_url = block.get("base_url")
    if base_url is not None:
        if known and provider != "openai":
            out.append(Problem(_HOUSEHOLD, "ai.base_url", f"the {provider} provider takes no address",
                               "remove it, or set `provider: openai` for a server that speaks OpenAI's interface"))
        elif not is_text(base_url):
            out.append(Problem(_HOUSEHOLD, "ai.base_url", "expected an address",
                               f"found {_found(base_url)}; write it as text, e.g. https://llm.example.net/v1"))
        elif (wrong := base_url_problem(base_url)) is not None:
            out.append(Problem(_HOUSEHOLD, "ai.base_url", wrong,
                               "write an https:// address, or http://127.0.0.1:<port>/v1 for a model on this "
                               "computer; the key goes in its own file, never in the address"))
    never_send = block.get("never_send")
    if never_send is not None and not _is_list_of_text(never_send):
        out.append(Problem(_HOUSEHOLD, "ai.never_send", "expected a list of words",
                           f"found {_found(never_send)}; write them as a list such as [Biscuit, Elmwood]"))
    accounts = block.get("accounts")
    if accounts is not None and not (isinstance(accounts, str) and accounts in AI_ACCOUNTS):
        out.append(Problem(_HOUSEHOLD, "ai.accounts", f"expected {' or '.join(AI_ACCOUNTS)}",
                           f"found {_found(accounts)}; write cards for the credit-card accounts only, or all"))
    for path, (default, low, high, whole) in AI_NUMBERS.items():
        node = block
        for part in path.split(".")[:-1]:
            node = node.get(part) if isinstance(node, dict) else None
        value = node.get(path.rsplit(".", 1)[-1]) if isinstance(node, dict) else None
        if value is None:
            continue
        ok = (_whole(value) if whole else is_number(value)) and low <= value <= high
        if not ok:
            what = "a whole number" if whole else "a number"
            out.append(Problem(_HOUSEHOLD, f"ai.{path}", f"expected {what} from {low} to {high}",
                               f"found {_found(value)}; leave it out for {default}"))
    return out


def ai_notes(doc: Mapping) -> list[str]:
    """What `validate` and the AI preview say without refusing household.yaml: when the AI step is configured (an
    `ai` block) and a member's name, the surname, the joint label or the household's name is written in letters other
    than Latin ones, one line saying that the bank prints names in Latin letters, so their Latin spelling belongs in
    `ai.never_send`."""
    if not isinstance(doc.get("ai"), dict):
        return []
    members = doc.get("members") if isinstance(doc.get("members"), list) else []
    names = [m.get("name") for m in members if isinstance(m, dict)]
    names += [doc.get("surname"), doc.get("joint_label"), doc.get("name")]
    found = ai_policy.unlatin_names(names)
    if not found:
        return []
    return [f"{_HOUSEHOLD}: ai: {', '.join(found)} {'is' if len(found) == 1 else 'are'} written in letters other "
            "than Latin ones, and banks print names in Latin letters — add the Latin spelling to ai.never_send so "
            "no vendor holding it is sent"]


# the most file names a shared_file_notes line names (it counts them all)
_FILES_NAMED = 5


def shared_file_notes(cfg) -> list[str]:
    """What `validate` says without refusing accounts.yaml when a statement file matches the patterns of two accounts
    whose formats both read it: one line per later account and earlier account, counting such files by their path in
    the statements folder (the first few named) and naming the account listed before it that reads them
    (Config.statement_file_readers); the ingest fails a check for that later account. A file a later account's format
    refuses is no note: it is simply the other account's."""
    readers = cfg.statement_file_readers()
    by_account: dict[str, dict[str, list[str]]] = {}
    for account_id in cfg.accounts:
        for source in cfg.txn_sources(account_id):
            for path in cfg.find_sources(source.glob):
                who = readers.get(path, [account_id])
                reader = who[0]
                name = path.relative_to(cfg.source_dir).as_posix() if path.is_relative_to(cfg.source_dir) else path.name
                if reader != account_id and account_id in who:
                    names = by_account.setdefault(account_id, {}).setdefault(reader, [])
                    if name not in names:
                        names.append(name)
    out = []
    for account_id, by_reader in by_account.items():
        for reader, names in by_reader.items():
            n, shown = len(names), ", ".join(sorted(names)[:_FILES_NAMED])
            more = f", and {n - _FILES_NAMED} more" if n > _FILES_NAMED else ""
            out.append(f"{_ACCOUNTS}: accounts.{account_id}: {n} statement file{'s' if n != 1 else ''} its pattern "
                       f"matches {'are' if n != 1 else 'is'} read for {reader}, the account listed before it: "
                       f"{shown}{more} — give each account a file pattern that matches only its own files")
    return out


def household_problems(doc: Mapping) -> list[Problem]:
    """household.yaml beyond the shape of its values (Config's own checks): unknown keys at the top, in a member and
    in `net_worth`; every `modules` key a known module (MODULE_IDS) and its value true or false; the scheduled refresh's
    times (refresh_problems); the time zone (timezone_problems); the AI step's settings (ai_problems)."""
    out = unknown_key_problems(_HOUSEHOLD, "", "", doc)
    members = doc.get("members")
    for i, m in enumerate(members if isinstance(members, list) else ()):
        if isinstance(m, dict):
            out += unknown_key_problems(_HOUSEHOLD, "members[]", f"members[{i}]", m)
    nw = doc.get("net_worth")
    if isinstance(nw, dict):
        out += unknown_key_problems(_HOUSEHOLD, "net_worth", "net_worth", nw)
    modules = doc.get("modules")
    for k, v in (modules.items() if isinstance(modules, dict) else ()):
        if k not in MODULE_IDS:
            out.append(Problem(_HOUSEHOLD, f"modules.{k}", f"unknown module {k!r}", f"one of: {', '.join(MODULE_IDS)}"))
        elif not isinstance(v, bool):
            out.append(Problem(_HOUSEHOLD, f"modules.{k}", "expected true or false",
                               "switch the module on with true or off with false"))
    return out + refresh_problems(doc) + timezone_problems(doc) + ai_problems(doc)


# ------------------------------------------------------------------------------------------------ accounts.yaml

# an account id names files and the dashboard's addresses
_ACCOUNT_ID = re.compile(r"[A-Za-z0-9_-]+")
_ID_FIX = "rename it, and every rule that names it, e.g. checking_0001"


def read_as_number(key) -> bool:
    """Is `key` a mapping key YAML built as a number (an id or a name written unquoted as digits)?"""
    return isinstance(key, (int, float)) and not isinstance(key, bool)


def quote_fix(key) -> str:
    """The fix for a key YAML read as a number: write it in quotes, so it is read as text."""
    return f"put it in quotes, e.g. \"{key}\": YAML reads an unquoted number as a number, not as text"


def _typed(where: str, value, test, what: str, fix: str) -> list[Problem]:
    """A problem at `where` unless `value` is absent (None) or passes `test`."""
    return [] if value is None or test(value) else [Problem(_ACCOUNTS, where, f"expected {what}", fix)]


def _class_fit(cls: str, kind: str, meta: Mapping) -> str | None:
    """What fits instead when the declared class `cls` does not fit an account of `kind`; None when it fits. A card or
    a loan is a debt and takes a debt class (Mortgage only on the `mortgage: true` loan); another kind takes none;
    Margin is derived, never declared."""
    if cls == "Margin":
        return ("Margin is derived from a margin: true brokerage's negative cash, never declared; use the account's "
                "own class")
    if kind in LIABILITY_KINDS:
        if cls == "Mortgage":
            if kind == "loan" and meta.get("mortgage") is True:
                return None
            return ("set mortgage: true on the account, or use Other Debt" if kind == "loan"
                    else "a credit-card account takes Other Debt")
        if cls in LIABILITY_CLASSES:
            return None
        return ("a loan is a debt: use Other Debt, or Mortgage on the mortgage: true loan" if kind == "loan"
                else "a credit-card account is a debt: use Other Debt")
    if cls in LIABILITY_CLASSES:
        return f"a {kind} account is no debt: use a class such as {DEFAULT_CLASS_BY_KIND[kind]}, or leave it out"
    return None


def _sources_problems(where: str, sources, parser_names: Collection[str] | None) -> list[Problem]:
    """An account's `sources:` (at key path `where`): a list of one or more mappings, each with the registry's keys, a
    known `format` (`parser_names`; None: not checked) and a `glob` relative to the statements folder that no other
    source of the account writes the same way (a file it matches would be read twice); the first with no `since`, each
    later one with a `since` after the one before it (a day either source could hold would be counted twice or not
    at all). Two different patterns that match one file are found at ingest, where that file fails its check."""
    where = f"{where}.sources"
    if not isinstance(sources, list) or not sources:
        return [Problem(_ACCOUNTS, where, "expected a list of one or more sources",
                        "write one `- {format: ..., glob: ...}` line per source, oldest first, each after the first "
                        "with the first day it holds, since: YYYY-MM-DD")]
    out = []
    formats = f", one of {', '.join(parser_names)}" if parser_names else ""
    globs: dict[str, int] = {}
    previous: tuple[int, str] | None = None          # the last source before this one whose start day reads
    for i, source in enumerate(sources):
        at = f"{where}[{i}]"
        if not isinstance(source, dict):
            out.append(Problem(_ACCOUNTS, at, "expected a mapping", "write {format: ..., glob: ...}"))
            continue
        out += unknown_key_problems(_ACCOUNTS, "accounts.<id>.sources[]", at, source)
        fmt, glob = source.get("format"), source.get("glob")
        if fmt is None:
            out.append(Problem(_ACCOUNTS, f"{at}.format", "missing",
                               f"name the format the source's files are read with{formats}"))
        elif not is_text(fmt):
            out.append(Problem(_ACCOUNTS, f"{at}.format", "expected text",
                               f"name the format the source's files are read with{formats}"))
        elif parser_names is not None and fmt not in parser_names:
            out.append(Problem(_ACCOUNTS, f"{at}.format", f"unknown format {fmt!r}", f"one of {', '.join(parser_names)}"))
        glob_fix = "write the file-name pattern of the source's files in quotes, e.g. \"sample-card-*.ofx\""
        if glob is None:
            out.append(Problem(_ACCOUNTS, f"{at}.glob", "missing",
                               "give the file-name pattern of the source's files, e.g. glob: \"sample-card-*.ofx\""))
        elif not is_text(glob):
            out.append(Problem(_ACCOUNTS, f"{at}.glob", "expected text", glob_fix))
        elif os.path.isabs(glob):
            out.append(Problem(_ACCOUNTS, f"{at}.glob", "write a pattern relative to the statements folder",
                               "an absolute path is not searched"))
        elif glob in globs:
            out.append(Problem(_ACCOUNTS, f"{at}.glob", f"the same pattern as sources[{globs[glob]}].glob",
                               "a file is read by one source: give each source a pattern of its own"))
        else:
            globs[glob] = i
        since, day = source.get("since"), iso_date(source.get("since"))
        day_fix = ("give the first day this source holds as YYYY-MM-DD, e.g. 2030-01-15; the source before it holds "
                   "the days before")
        if i == 0:
            if since is not None:
                out.append(Problem(_ACCOUNTS, f"{at}.since", "the first source has no start day",
                                   "remove it: the first source holds every day before the second one's since"))
        elif since is None:
            out.append(Problem(_ACCOUNTS, f"{at}.since", "missing", day_fix))
        elif day is None:
            out.append(Problem(_ACCOUNTS, f"{at}.since", "expected an ISO date", day_fix))
        elif previous is not None and day <= previous[1]:
            # compared with the latest start day so far, so every source out of order is named
            out.append(Problem(_ACCOUNTS, f"{at}.since", f"not after sources[{previous[0]}].since ({previous[1]})",
                               "list the sources oldest first, each since: later than the one before it"))
        else:
            previous = (i, day)
    return out


def _account_problems(aid, meta, parser_names: Collection[str] | None, *, feed_only: bool = False) -> list[Problem]:
    """One `accounts:` entry: its id, its keys, the required text keys, the class against the kind, the statement
    source, the feed keys, the cutover a file source with a feed id needs (an ingest's matter, so not with
    `feed_only`) and the flags that only some kinds may carry. Kinds, classes, owners and `loan:` are Config's own
    checks."""
    where = f"accounts.{aid}"
    out = []
    if not (isinstance(aid, str) and _ACCOUNT_ID.fullmatch(aid)):
        out.append(Problem(_ACCOUNTS, where, "account ids use letters, digits, _ and -",
                           quote_fix(aid) if read_as_number(aid) else _ID_FIX))
    if not isinstance(meta, dict):
        return [*out, Problem(_ACCOUNTS, where, "expected a mapping",
                              "write name:, institution:, owner: and kind: under the account's id")]
    out += unknown_key_problems(_ACCOUNTS, "accounts.<id>", where, meta)
    for key, fix in (("name", "give the account the name the dashboard shows, e.g. name: Sample Checking"),
                     ("institution", "name the bank or broker that holds it, e.g. institution: Sample Bank")):
        if meta.get(key) is None:
            out.append(Problem(_ACCOUNTS, f"{where}.{key}", "missing", fix))
        elif not is_text(meta[key]):
            out.append(Problem(_ACCOUNTS, f"{where}.{key}", "expected text", fix))
    if "owner" not in meta:
        out.append(Problem(_ACCOUNTS, f"{where}.owner", "missing",
                           "name its owner: an adult, the joint label or a child from household.yaml"))
    kind = meta.get("kind")
    known_kind = isinstance(kind, str) and kind in ACCOUNT_KINDS
    cls = meta.get("asset_class")
    if known_kind and isinstance(cls, str) and cls in ASSET_CLASSES:
        fits = _class_fit(cls, kind, meta)
        if fits is not None:
            out.append(Problem(_ACCOUNTS, f"{where}.asset_class", f"'{cls}' does not fit a {kind} account", fits))

    parser, glob, sources = meta.get("parser"), meta.get("glob"), meta.get("sources")
    formats = f", one of {', '.join(parser_names)}" if parser_names else ""
    if parser is not None and not is_text(parser):
        out.append(Problem(_ACCOUNTS, f"{where}.parser", "expected text", f"name its statement format{formats}"))
    elif parser is not None and parser_names is not None and parser not in parser_names:
        out.append(Problem(_ACCOUNTS, f"{where}.parser", f"unknown parser {parser!r}",
                           f"one of {', '.join(parser_names)}"))
    out += _typed(f"{where}.glob", glob, is_text, "text",
                  "write the file-name pattern of its statement files in quotes, e.g. \"sample-checking-*.csv\"")
    if is_text(glob) and os.path.isabs(glob):
        out.append(Problem(_ACCOUNTS, f"{where}.glob", "write a pattern relative to the statements folder",
                           "an absolute path is not searched"))
    if sources is not None:
        out += _sources_problems(where, sources, parser_names)
        both = [k for k in ("parser", "glob") if meta.get(k) is not None]
        if both:
            out.append(Problem(_ACCOUNTS, f"{where}.sources", f"given with {' and '.join(both)}",
                               "state the account's statement files one way: move the format and the pattern into "
                               "sources, or remove sources"))
    elif glob and not parser:
        out.append(Problem(_ACCOUNTS, f"{where}.parser", "missing, though the account has a glob",
                           f"name the format its statement files are read with{formats}, or remove glob"))
    elif parser and not glob:
        out.append(Problem(_ACCOUNTS, f"{where}.glob", "missing, though the account has a parser",
                           "give the file-name pattern of its statement files, e.g. glob: \"sample-checking-*.csv\", "
                           "or remove parser"))

    out += _typed(f"{where}.simplefin_id", meta.get("simplefin_id"), is_text, "text",
                  "write the feed's id for the account in quotes")
    out += _typed(f"{where}.simplefin_id_history", meta.get("simplefin_id_history"), _is_list_of_text,
                  "a list of text", "list the account's earlier feed ids, e.g. [\"ACT-0000\"]")
    cutover = meta.get("simplefin_cutover")
    if cutover is not None and iso_date(cutover) is None:
        out.append(Problem(_ACCOUNTS, f"{where}.simplefin_cutover", "expected an ISO date",
                           "write the first day the feed owns as YYYY-MM-DD, e.g. 2030-01-15"))
    out += _typed(f"{where}.simplefin_balances_only", meta.get("simplefin_balances_only"),
                  lambda v: isinstance(v, bool), "true or false", "write true or false, or leave it out")
    # with balances only the feed brings no row, yet a cutover would still drop the file rows from its day on once the
    # feed reports a balance: rows stored nowhere (an ingest's matter, as the cutover itself is)
    if not feed_only and cutover is not None and meta.get("simplefin_balances_only") is True:
        out.append(Problem(_ACCOUNTS, f"{where}.simplefin_cutover",
                           "with simplefin_balances_only: true the feed brings no transactions, so there is nothing "
                           "to cut over to", "remove one of the two"))
    # from the cutover on the feed owns the rows, so a source starting on or after it would keep none once the feed
    # reports the account
    if not feed_only and isinstance(sources, list) and iso_date(cutover) is not None:
        for i, source in enumerate(sources[1:], 1):
            since = iso_date(source.get("since")) if isinstance(source, dict) else None
            if since is not None and since >= iso_date(cutover):
                out.append(Problem(_ACCOUNTS, f"{where}.sources[{i}].since",
                                   f"on or after the account's simplefin_cutover ({iso_date(cutover)})",
                                   "the feed owns the days from the cutover on, so this source would keep no row: give "
                                   "it a since before the cutover, or move the cutover"))
    # statement files and the feed with no date between them would count every transaction they share twice; an
    # earlier feed id counts like a current one, since its snapshots are still read
    if (not feed_only and (parser is not None or glob is not None or sources is not None)
            and (meta.get("simplefin_id") is not None or meta.get("simplefin_id_history"))
            and cutover is None and meta.get("simplefin_balances_only") is not True):
        out.append(Problem(_ACCOUNTS, where, "has a file source and a feed id but no cutover",
                           "add simplefin_cutover: <date> or simplefin_balances_only: true"))
    out += _typed(f"{where}.tags", meta.get("tags"), _is_list_of_text, "a list of text",
                  "write the tags as a list, e.g. [Business]")
    out += _typed(f"{where}.note", meta.get("note"), lambda v: isinstance(v, str), "text", "write the note as text")

    limit = meta.get("credit_limit")
    if limit is not None and known_kind and kind != "credit-card":
        out.append(Problem(_ACCOUNTS, f"{where}.credit_limit", "only a credit card has a credit limit",
                           "remove it, or set kind: credit-card"))
    elif limit is not None and not (is_number(limit) and limit >= 0):
        out.append(Problem(_ACCOUNTS, f"{where}.credit_limit", "expected an amount of 0 or more",
                           "write the card's limit in dollars, e.g. 5000"))
    for flag, only, what in (("margin", "brokerage", "a margin account"), ("mortgage", "loan", "the mortgage")):
        value = meta.get(flag)
        if value is not None and not isinstance(value, bool):
            out.append(Problem(_ACCOUNTS, f"{where}.{flag}", "expected true or false",
                               f"write {flag}: true, or leave it out"))
        elif value is True and known_kind and kind != only:
            out.append(Problem(_ACCOUNTS, f"{where}.{flag}", f"only a {only} account can be {what}",
                               f"remove {flag}, or set kind: {only}"))
    return out


def _registry_problems(accounts: Mapping, parser_names: Collection[str] | None, *,
                       feed_only: bool = False) -> list[Problem]:
    """Every account (_account_problems), then what spans accounts: one account per feed id (its `simplefin_id` and
    the ids in its `simplefin_id_history` together) and at most one `mortgage: true` loan (the flag on another kind
    is _account_problems' own problem)."""
    out = []
    for aid, meta in accounts.items():
        out += _account_problems(aid, meta, parser_names, feed_only=feed_only)
    first: dict[str, str] = {}
    for aid, meta in accounts.items():
        if not isinstance(meta, dict):
            continue
        history = meta.get("simplefin_id_history")
        ids = [(f"accounts.{aid}.simplefin_id", meta.get("simplefin_id")),
               *((f"accounts.{aid}.simplefin_id_history[{j}]", x)
                 for j, x in enumerate(history if isinstance(history, list) else ()))]
        for path, sfid in ids:
            if not is_text(sfid):
                continue
            if sfid in first:
                out.append(Problem(_ACCOUNTS, path, f"feed id {sfid!r} is also at {first[sfid]}",
                                   "a feed account belongs to one account: keep the id in one place"))
            else:
                first[sfid] = path
    mortgages = [aid for aid, meta in accounts.items()
                 if isinstance(meta, dict) and meta.get("mortgage") is True and meta.get("kind") == "loan"]
    out += [Problem(_ACCOUNTS, f"accounts.{aid}.mortgage", f"a second mortgage account (accounts.{mortgages[0]} is one)",
                    "keep mortgage: true on one loan; the engine reads one mortgage")
            for aid in mortgages[1:]]
    return out


def _manual_entry_problems(entries: list) -> list[Problem]:
    """`manual_entries`: each a mapping of the registry's keys with its required ones present and each value of its
    type, a name whose slug (networth.manual_slug, the entry's id) is not empty and at most MAX_MANUAL_ID_LEN
    characters, and no debt marked liquid. Unknown owners and classes and two entries of one name are Config's own
    checks."""
    out = []
    for i, e in enumerate(entries):
        where = f"manual_entries[{i}]"
        if not isinstance(e, dict):
            out.append(Problem(_ACCOUNTS, where, "expected a mapping",
                               "write {account: ..., institution: ..., owner: ..., asset_class: ..., value: ..., "
                               "as_of: ...}"))
            continue
        out += unknown_key_problems(_ACCOUNTS, "manual_entries[]", where, e)
        for key, fix in (("account", "name the entry, e.g. account: Sample House"),
                         ("institution", "name who holds or values it, e.g. institution: Sample Bank")):
            if e.get(key) is None:
                out.append(Problem(_ACCOUNTS, f"{where}.{key}", "missing", fix))
            elif not is_text(e[key]):
                out.append(Problem(_ACCOUNTS, f"{where}.{key}", "expected text", fix))
        # the entry's id is its name's slug, which an app asset carries to override it (manual_assets.is_id)
        slug = manual_slug(e["account"]) if is_text(e.get("account")) else None
        if slug == "":
            out.append(Problem(_ACCOUNTS, f"{where}.account", "has no letters or digits to make its id of",
                               "give it a name with letters or digits"))
        elif slug is not None and len(slug) > MAX_MANUAL_ID_LEN:
            out.append(Problem(_ACCOUNTS, f"{where}.account",
                               f"makes an id of {len(slug)} characters, over {MAX_MANUAL_ID_LEN}", "shorten the name"))
        if "owner" not in e:
            out.append(Problem(_ACCOUNTS, f"{where}.owner", "missing",
                               "name its owner: an adult, the joint label or a child from household.yaml"))
        if "asset_class" not in e:
            out.append(Problem(_ACCOUNTS, f"{where}.asset_class", "missing", f"one of {', '.join(ASSET_CLASSES)}"))
        value_fix = "write the value in dollars as a number, e.g. 350000 (negative for a debt)"
        if e.get("value") is None:
            out.append(Problem(_ACCOUNTS, f"{where}.value", "missing", value_fix))
        elif not is_number(e["value"]):
            out.append(Problem(_ACCOUNTS, f"{where}.value", "expected a number", value_fix))
        elif not -MAX_MANUAL_VALUE <= e["value"] <= MAX_MANUAL_VALUE:     # its cents must fit the database
            out.append(Problem(_ACCOUNTS, f"{where}.value", "outside -1e12 to 1e12 dollars", value_fix))
        date_fix = "write the day the value was true as YYYY-MM-DD, e.g. 2030-01-31"
        if e.get("as_of") is None:
            out.append(Problem(_ACCOUNTS, f"{where}.as_of", "missing", date_fix))
        elif iso_date(e["as_of"]) is None:
            out.append(Problem(_ACCOUNTS, f"{where}.as_of", "expected an ISO date", date_fix))
        acquired_fix = "write the day it was bought as YYYY-MM-DD, on or before as_of, or leave acquired out"
        if e.get("acquired") is not None:
            acquired, as_of = iso_date(e["acquired"]), iso_date(e.get("as_of"))
            if acquired is None:
                out.append(Problem(_ACCOUNTS, f"{where}.acquired", "expected an ISO date", acquired_fix))
            elif as_of is not None and acquired > as_of:
                out.append(Problem(_ACCOUNTS, f"{where}.acquired", f"after the entry's as_of ({as_of})", acquired_fix))
        liquid = e.get("liquid")
        if liquid is not None and not isinstance(liquid, bool):
            out.append(Problem(_ACCOUNTS, f"{where}.liquid", "expected true or false",
                               "write liquid: true or false, or leave it out (false)"))
        elif liquid is True and isinstance(e.get("asset_class"), str) and e["asset_class"] in LIABILITY_CLASSES:
            out.append(Problem(_ACCOUNTS, f"{where}.liquid", "a debt is never liquid", "remove liquid or set it false"))
        out += _typed(f"{where}.note", e.get("note"), lambda v: isinstance(v, str), "text", "write the note as text")
    return out


def accounts_problems(doc: Mapping, parser_names: Collection[str] | None, *, feed_only: bool = False) -> list[Problem]:
    """accounts.yaml beyond Config's own checks: `version`, the type of every top-level block, unknown keys (at the
    top, in an account, a manual entry, `simplefin:` and `household:`), every account and what spans accounts
    (_registry_problems), the manual entries and the display names (_display_name_problems). `parser_names` are the
    statement formats a `parser:` may name (None: not checked). `feed_only` keeps what the bank-feed commands read: the
    version, the `accounts:` registry and the `simplefin:` block."""
    out = version_problems(_ACCOUNTS, doc)
    accounts = doc.get("accounts")
    if accounts is None:
        out.append(Problem(_ACCOUNTS, "accounts", "missing",
                           "list the household's accounts under accounts:, each id with its name, institution, owner "
                           "and kind"))
    elif not isinstance(accounts, dict):
        out.append(Problem(_ACCOUNTS, "accounts", "expected a mapping",
                           "write each account as `<id>:` with name:, institution:, owner: and kind: under it"))
    else:
        out += _registry_problems(accounts, parser_names, feed_only=feed_only)
    simplefin = doc.get("simplefin")
    out += _typed("simplefin", simplefin, lambda v: isinstance(v, dict), "a mapping",
                  "write store_dir: and ignore: under it, or leave it out")
    if isinstance(simplefin, dict):
        out += unknown_key_problems(_ACCOUNTS, "simplefin", "simplefin", simplefin)
        out += _typed("simplefin.store_dir", simplefin.get("store_dir"), is_text, "text",
                      "write the feed store's folder, relative to the folder that holds config/")
        out += _typed("simplefin.ignore", simplefin.get("ignore"), _is_list_of_text, "a list of text",
                      "list the feed account ids to leave unmapped, e.g. [\"ACT-0000\"]")
    if feed_only:
        return out
    out += unknown_key_problems(_ACCOUNTS, "", "", doc)
    out += _typed("source_dir", doc.get("source_dir"), is_text, "text",
                  "write the statements folder's path, relative to the folder that holds config/")
    out += _typed("skip_files", doc.get("skip_files"), _is_list_of_text, "a list of text",
                  "list file-name patterns never to read, e.g. [\"*-old.csv\"]")
    household = doc.get("household")
    out += _typed("household", household, lambda v: isinstance(v, dict), "a mapping",
                  "write own_name_tokens: under it, or leave it out")
    if isinstance(household, dict):
        out += unknown_key_problems(_ACCOUNTS, "household", "household", household)
        out += _typed("household.own_name_tokens", household.get("own_name_tokens"), _is_list_of_text,
                      "a list of text", "list the words, e.g. [ALEX, SAM, SAMPLE]")
    manual = doc.get("manual_entries")
    out += _typed("manual_entries", manual, lambda v: isinstance(v, list), "a list",
                  "write one `- {account: ..., institution: ..., ...}` entry per value")
    if isinstance(manual, list):
        out += _manual_entry_problems(manual)
    if isinstance(accounts, dict):
        out += _display_name_problems(accounts, manual if isinstance(manual, list) else [])
    return out


_NAME_FIX = "give each account its own name (the dashboard tells accounts apart by it)"
_ENTRY_NAME_FIX = "rename the entry or the account (the dashboard tells accounts apart by name)"


def _display_name_problems(accounts: Mapping, manual: list) -> list[Problem]:
    """Every account's `name` is its own, and no manual entry's `account` is an account's name, compared trimmed and
    ignoring case: the dashboard lists and joins accounts by name, so two of one name would lose a balance. The later
    one names the first; a name that is no text is the type check's."""
    first: dict[str, str] = {}
    out = []
    for aid, meta in accounts.items():
        name = meta.get("name") if isinstance(meta, dict) else None
        if not is_text(name):
            continue
        key = name.strip().casefold()
        if key in first:
            out.append(Problem(_ACCOUNTS, f"accounts.{aid}.name", f"same name as accounts.{first[key]}", _NAME_FIX))
        else:
            first[key] = aid
    for i, e in enumerate(manual):
        name = e.get("account") if isinstance(e, dict) else None
        if is_text(name) and name.strip().casefold() in first:
            out.append(Problem(_ACCOUNTS, f"manual_entries[{i}].account",
                               f"same name as accounts.{first[name.strip().casefold()]}", _ENTRY_NAME_FIX))
    return out


def statements_dir_problem(path: Path) -> Problem:
    """The statements folder accounts.yaml `source_dir` names does not exist (only the developer layout reads that key;
    FF_IMPORTS_DIR and FF_HOME name the folder otherwise)."""
    return Problem(_ACCOUNTS, "source_dir", f"the statements folder {path} does not exist",
                   "create it, correct source_dir (it is relative to the folder that holds config/), or set "
                   "FF_IMPORTS_DIR to the folder")


# ------------------------------------------------------------------------------------------------ who may be named

@dataclass(frozen=True)
class Roster:
    """Who the other files' `owner:` and `earner:` keys may name, read from the household.yaml members that are
    well-formed: `owners` (those adults, the joint label, those children, each group in file order), `earners` (those
    members: the joint label earns nothing) and `unchecked`, the names of the members whose own entry has a problem —
    a key naming one of them is not reported until that entry is fixed."""
    owners: tuple[str, ...]
    earners: tuple[str, ...]
    unchecked: frozenset[str] = frozenset()

    def knows_owner(self, who) -> bool:
        return isinstance(who, str) and (who in self.owners or who in self.unchecked)

    def knows_earner(self, who) -> bool:
        return isinstance(who, str) and (who in self.earners or who in self.unchecked)


def earner_problem(file: str, path: str, who, roster: Roster) -> Problem | None:
    """An `earner:` value that is no member of household.yaml (the joint label is none: a joint account does not
    earn), else None."""
    if roster.knows_earner(who):
        return None
    members = ", ".join(roster.earners)
    if roster.knows_owner(who):
        return Problem(file, path, f"{who!r} is the joint label, which earns nothing",
                       f"name the member who earned it: one of {members}")
    return Problem(file, path, f"unknown earner {who!r}", f"one of: {members}")


# ------------------------------------------------------------------------------------------------ the rule files

_ASSUMPTIONS = "assumptions.yaml"
_CATEGORY_RULES = "category_rules.yaml"
_VENDOR_RULES = "vendor_rules.yaml"
_TRANSFER_RULES = "transfer_rules.yaml"
_BANK_MAP = "bank_category_map.yaml"
_OVERRIDES = "overrides.yaml"
_BY_PATH = {(k.file, k.path): k for k in KEYS}
_FIX_REGEX = "correct the pattern (a Python regular expression, matched with case ignored)"
_FIX_ACCOUNT = "use an account id from accounts.yaml"
# the transaction types an income rule may give a deposit, and the keys an override's match: reads
_INCOME_RULE_TYPES = ("income", "dividend", "interest")
MATCH_KEYS = ("uid", "vendor", "account", "date", "amount", "contains")


def _items(v) -> list:
    return v if isinstance(v, list) else []


def regex_error(pattern) -> str | None:
    """Why `pattern` is no regular expression the engine can use (it compiles each with case ignored), or None."""
    if not isinstance(pattern, str):
        return "expected text"
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return str(e)
    return None


def block_problems(file: str, pattern: str, where: str, block: Mapping) -> list[Problem]:
    """A block's unknown keys (unknown_key_problems) and each known key whose registry type has a test (_TYPE_TESTS)
    but whose value fails it; a null value reads as absent. A key of any other registry type is its own check's."""
    out = unknown_key_problems(file, pattern, where, block)
    for key, value in block.items():
        known = _BY_PATH.get((file, _join(pattern, key)))
        test = _TYPE_TESTS.get(known.type) if known is not None else None
        if value is not None and test is not None and not test(value):
            out.append(Problem(file, _join(where, key), f"expected {_ARTICLE[known.type]}",
                               f"write {_TYPE_WRITE[known.type]}, or leave the key out"))
    return out


def taxonomy_problems(file: str, where: str, category, subcategory, taxonomy: Mapping) -> list[Problem]:
    """`category` (at `where`.category) must be a category of the taxonomy and `subcategory`, when given, one listed
    under it (the internal category's subcategories are the engine's own, so any passes); values that are no text are
    the type checks' to report."""
    if not isinstance(category, str):
        return []
    if category not in taxonomy:
        return [Problem(file, f"{where}.category", f"category {category!r} is not in the taxonomy",
                        "use a category listed under taxonomy in category_rules.yaml, or add it there")]
    listed = taxonomy.get(category) or []
    if isinstance(subcategory, str) and category != INTERNAL_CATEGORY and subcategory not in listed:
        return [Problem(file, f"{where}.subcategory", f"subcategory {subcategory!r} is not under {category!r} in the "
                        "taxonomy", f"use one listed under taxonomy.{category} in category_rules.yaml, or add it there")]
    return []


def _account_list_problems(file: str, where: str, value, account_ids: Collection[str] | None) -> list[Problem]:
    """An `accounts:` list: registry account ids (not checked when `account_ids` is None)."""
    if not isinstance(value, list):
        return [Problem(file, where, "expected a list of account ids", "list accounts.yaml ids, e.g. "
                                                                       "[demo_checking_0000]")]
    if account_ids is None:
        return []
    return [Problem(file, where, f"unknown account {a!r}", _FIX_ACCOUNT)
            for a in value if not (isinstance(a, str) and a in account_ids)]


def _income_rule_problems(i: int, r, *, taxonomy: Mapping | None, roster: Roster | None,
                          account_ids: Collection[str] | None) -> list[Problem]:
    """One `income_rules` entry: its keys and their types, a pattern that compiles, a type of income, the registry
    accounts it reads, a subcategory under the income category of the taxonomy and an earner who is a member."""
    file, where = _CATEGORY_RULES, f"income_rules[{i}]"
    if not isinstance(r, dict):
        return [Problem(file, where, "expected a mapping", "write `{pattern: ..., subcategory: ..., earner: ...}`")]
    out = block_problems(file, "income_rules[]", where, r)
    pattern = r.get("pattern")
    if pattern in (None, ""):
        out.append(Problem(file, f"{where}.pattern", "missing",
                           "write the regular expression a deposit's description is matched against"))
    elif (error := regex_error(pattern)) is not None:
        out.append(Problem(file, f"{where}.pattern", f"is not a valid regular expression ({error})", _FIX_REGEX))
    kind = r.get("type", "income")
    if not (isinstance(kind, str) and kind in _INCOME_RULE_TYPES):
        out.append(Problem(file, f"{where}.type", f"unknown type {kind!r}",
                           f"one of {', '.join(_INCOME_RULE_TYPES)}, or leave it out (income)"))
    if r.get("accounts") is not None:
        out += _account_list_problems(file, f"{where}.accounts", r["accounts"], account_ids)
    sub = r.get("subcategory")
    if taxonomy is not None and isinstance(sub, str) and sub not in (taxonomy.get(INCOME_CATEGORY) or []):
        out.append(Problem(file, f"{where}.subcategory", f"subcategory {sub!r} is not under {INCOME_CATEGORY!r} in the "
                           "taxonomy", f"use one listed under taxonomy.{INCOME_CATEGORY}, or add it there"))
    if roster is not None and r.get("earner") is not None:
        found = earner_problem(file, f"{where}.earner", r["earner"], roster)
        out += [found] if found else []
    return out


def keyword_rule_problems(i: int, r) -> list[Problem]:
    """The unknown keys of the keyword rule at index `i` of category_rules.yaml (`r`), in the rule, its conditions and
    its `set:`; a rule that is no mapping is the compiler's problem. `rules preview` runs this on its candidates."""
    if not isinstance(r, dict):
        return []
    file = _CATEGORY_RULES
    found = unknown_key_problems(file, "keyword_rules[]", f"keyword_rules[{i}]", r)
    for op in ("all", "any"):
        for j, c in enumerate(_items(r.get(op))):
            if isinstance(c, dict):
                found += unknown_key_problems(file, f"keyword_rules[].{op}[]", f"keyword_rules[{i}].{op}[{j}]", c)
    if isinstance(r.get("set"), dict):
        found += unknown_key_problems(file, "keyword_rules[].set", f"keyword_rules[{i}].set", r["set"])
    return found


def category_rules_problems(doc: Mapping, *, taxonomy: Mapping | None, roster: Roster | None,
                            account_ids: Collection[str] | None) -> tuple[list[Problem], set[int], set[int]]:
    """category_rules.yaml beyond its compilers: unknown keys at the top, in a keyword rule (of either shape), its
    conditions and its `set:`, and in a rename and its `from:` and `to:`; each income rule (_income_rule_problems).
    Returns the problems and the indexes of the keyword rules and of the renames with an unknown key, which the
    compilers then leave out."""
    file = _CATEGORY_RULES
    out = unknown_key_problems(file, "", "", doc)
    skip_rules: set[int] = set()
    for i, r in enumerate(_items(doc.get("keyword_rules"))):
        found = keyword_rule_problems(i, r)
        if found:
            skip_rules.add(i)
            out += found
    skip_renames = _rename_key_problems(file, doc, out, sides=("from", "to"))
    for i, r in enumerate(_items(doc.get("income_rules"))):
        out += _income_rule_problems(i, r, taxonomy=taxonomy, roster=roster, account_ids=account_ids)
    return out, skip_rules, skip_renames


def _rename_key_problems(file: str, doc: Mapping, out: list[Problem], *, sides: tuple[str, ...] = ()) -> set[int]:
    """Unknown keys of each `renames:` entry (and of its `from:` / `to:` mappings, `sides`), appended to `out`; returns
    the indexes of the entries that have one."""
    skip = set()
    for i, r in enumerate(_items(doc.get("renames"))):
        if not isinstance(r, dict):
            continue                                   # the renamer's problem
        found = unknown_key_problems(file, "renames[]", f"renames[{i}]", r)
        for side in sides:
            if isinstance(r.get(side), dict):
                found += unknown_key_problems(file, f"renames[].{side}", f"renames[{i}].{side}", r[side])
        if found:
            skip.add(i)
            out += found
    return skip


def vendor_rules_problems(doc: Mapping) -> tuple[list[Problem], set[int], set[int]]:
    """vendor_rules.yaml beyond its compilers: unknown keys at the top, in a pattern and its split legs, in
    `learning:` and in a rename; each `strip_prefixes` and `cleanup` pattern compiles. Returns the problems and the
    indexes of the patterns and of the renames with an unknown key, which the compilers then leave out."""
    file = _VENDOR_RULES
    out = unknown_key_problems(file, "", "", doc)
    for key in ("strip_prefixes", "cleanup"):
        for j, p in enumerate(_items(doc.get(key))):
            if isinstance(p, str) and (error := regex_error(p)) is not None:
                out.append(Problem(file, f"{key}[{j}]", f"is not a valid regular expression ({error})", _FIX_REGEX))
    skip_patterns: set[int] = set()
    for i, e in enumerate(_items(doc.get("patterns"))):
        if not isinstance(e, dict) or "pattern" not in e:
            continue                                   # the shape check's problem
        found = unknown_key_problems(file, "patterns[]", f"patterns[{i}]", e)
        for j, leg in enumerate(_items(e.get("split"))):
            if isinstance(leg, dict):
                found += unknown_key_problems(file, "patterns[].split[]", f"patterns[{i}].split[{j}]", leg)
        if found:
            skip_patterns.add(i)
            out += found
    if isinstance(doc.get("learning"), dict):
        out += unknown_key_problems(file, "learning", "learning", doc["learning"])
    return out, skip_patterns, _rename_key_problems(file, doc, out)


def transfer_rules_problems(doc: Mapping) -> list[Problem]:
    """transfer_rules.yaml beyond transfers.transfer_rule_problems (which judges each value): unknown keys at the top,
    in a rule, a group, `venmo_patterns:` and a `cashout_income` entry, and the type of a rule's plain keys (name,
    bank_type, needs_review, open_question, note)."""
    file = _TRANSFER_RULES
    out = unknown_key_problems(file, "", "", doc)
    for i, r in enumerate(_items(doc.get("rules"))):
        if isinstance(r, dict):
            out += block_problems(file, "rules[]", f"rules[{i}]", r)
    groups = doc.get("groups")
    for name, g in groups.items() if isinstance(groups, dict) else ():
        if isinstance(g, dict):
            out += unknown_key_problems(file, "groups.<name>", f"groups.{name}", g)
    vp = doc.get("venmo_patterns")
    if isinstance(vp, dict):
        out += unknown_key_problems(file, "venmo_patterns", "venmo_patterns", vp)
        for i, e in enumerate(_items(vp.get("cashout_income"))):
            if isinstance(e, dict):
                out += unknown_key_problems(file, "venmo_patterns.cashout_income[]",
                                            f"venmo_patterns.cashout_income[{i}]", e)
    return out


def bank_map_problems(doc: Mapping, taxonomy: Mapping | None) -> list[Problem]:
    """bank_category_map.yaml: each label's entry holds known keys of their types, a category (in the taxonomy) and
    an optional subcategory under it. An entry that is no mapping is rule_file_problems'."""
    file = _BANK_MAP
    out = []
    for label, entry in doc.items():
        if label == "version" or not isinstance(entry, dict):
            continue
        out += block_problems(file, "<label>", str(label), entry)
        if entry.get("category") is None:
            out.append(Problem(file, f"{label}.category", "missing",
                               "name the category its rows are filed under, e.g. category: Dining"))
        elif taxonomy is not None:
            out += taxonomy_problems(file, str(label), entry["category"], entry.get("subcategory"), taxonomy)
    return out


def _match_problems(where: str, match, account_ids: Collection[str] | None) -> list[Problem]:
    """An override's `match:`: a mapping of MATCH_KEYS, at least one, each value of its kind."""
    file = _OVERRIDES
    if match is None:
        return [Problem(file, f"{where}.match", "missing",
                        f"say what a transaction must have: one or more of {', '.join(MATCH_KEYS)}")]
    if not isinstance(match, dict):
        return [Problem(file, f"{where}.match", "expected a mapping", "write `match: {vendor: ..., date: ...}`")]
    out = unknown_key_problems(file, "[].match", f"{where}.match", match)
    if not any(k in MATCH_KEYS for k in match):
        out.append(Problem(file, f"{where}.match", "matches nothing",
                           f"give it one or more of {', '.join(MATCH_KEYS)}"))
    checks = {"uid": (is_text, "text", "write the transaction's id"),
              "vendor": (is_text, "text", "write the vendor's name"),
              "contains": (is_text, "text", "write words its description contains"),
              "date": (lambda v: iso_date(v) is not None, "an ISO date", "write the day as YYYY-MM-DD"),
              "amount": (is_number, "a number", "write the amount in dollars, negative for money out")}
    for key, (test, what, fix) in checks.items():
        if key in match and not test(match[key]):
            out.append(Problem(file, f"{where}.match.{key}", f"expected {what}", fix))
    if "account" in match:
        account = match["account"]
        if not is_text(account):
            out.append(Problem(file, f"{where}.match.account", "expected an account id", _FIX_ACCOUNT))
        elif account_ids is not None and account not in account_ids:
            out.append(Problem(file, f"{where}.match.account", f"unknown account {account!r}", _FIX_ACCOUNT))
    return out


def _set_problems(where: str, set_, *, taxonomy: Mapping | None, roster: Roster | None, rename) -> list[Problem]:
    """An override's `set:`: a mapping of known keys, each value of its kind; its category (as the category renames
    leave it, `rename`) in the taxonomy and its subcategory under it; its earner a member."""
    file = _OVERRIDES
    if set_ is None:
        return [Problem(file, f"{where}.set", "missing",
                        "say what a matched transaction is given, e.g. set: {category: Dining}")]
    if not isinstance(set_, dict):
        return [Problem(file, f"{where}.set", "expected a mapping", "write `set: {category: ..., subcategory: ...}`")]
    out = unknown_key_problems(file, "[].set", f"{where}.set", set_)
    if "type" in set_ and not (isinstance(set_["type"], str) and set_["type"] in TXN_TYPES):
        out.append(Problem(file, f"{where}.set.type", f"unknown type {set_['type']!r}", f"one of {', '.join(TXN_TYPES)}"))
    if "category" in set_ and not is_text(set_["category"]):
        out.append(Problem(file, f"{where}.set.category", "expected text", "write a category of the taxonomy"))
    if set_.get("subcategory") is not None and not is_text(set_["subcategory"]):
        out.append(Problem(file, f"{where}.set.subcategory", "expected text",
                           "write a subcategory of the taxonomy, or null to clear it"))
    if "tags" in set_ and not _is_list_of_text(set_["tags"]):
        out.append(Problem(file, f"{where}.set.tags", "expected a list of text", "write the tags as a list, e.g. "
                                                                                 "[Household]"))
    if "needs_review" in set_ and not isinstance(set_["needs_review"], bool):
        out.append(Problem(file, f"{where}.set.needs_review", "expected true or false",
                           "write true to send it to review, false to take it out"))
    if taxonomy is not None and is_text(set_.get("category")):
        category, sub = rename(set_["category"], set_.get("subcategory"))
        out += taxonomy_problems(file, f"{where}.set", category, sub, taxonomy)
    kind = set_.get("type")
    if (is_text(set_.get("category")) and rename(set_["category"], set_.get("subcategory"))[0] == INTERNAL_CATEGORY
            and not (isinstance(kind, str) and kind in INTERNAL_TYPES)):
        # the type alone takes a row out of spending and income: filed Internal without one, it still counts
        out.append(Problem(file, f"{where}.set.category", f"category {INTERNAL_CATEGORY!r} with no internal type "
                           "still counts the row in spending or income",
                           "set type: transfer (transfer_rules.yaml or an override) instead"))
    if roster is not None and set_.get("earner") is not None:
        found = earner_problem(file, f"{where}.set.earner", set_["earner"], roster)
        out += [found] if found else []
    return out


def override_problems(entries: list, *, taxonomy: Mapping | None, roster: Roster | None,
                      account_ids: Collection[str] | None, rename=lambda c, s: (c, s)) -> list[Problem]:
    """overrides.yaml: each entry a mapping of `match`, `set` and an optional `note`; the match can match something
    (_match_problems) and the set is one the engine can apply (_set_problems)."""
    file, out = _OVERRIDES, []
    for i, e in enumerate(entries):
        where = f"[{i}]"
        if not isinstance(e, dict):
            out.append(Problem(file, where, "expected a mapping", "write `- {match: {...}, set: {...}}`"))
            continue
        out += unknown_key_problems(file, "[]", where, e)
        if e.get("note") is not None and not isinstance(e["note"], str):
            out.append(Problem(file, f"{where}.note", "expected text", "write the note as text"))
        out += _match_problems(where, e.get("match"), account_ids)
        out += _set_problems(where, e.get("set"), taxonomy=taxonomy, roster=roster, rename=rename)
    return out


# the assumptions.yaml thresholds: key -> (whole number?, its default)
_THRESHOLDS = {"match_window_bdays": (True, 3), "venmo_match_window_bdays": (True, 5),
               "venmo_review_threshold": (False, 200), "uncategorized_review_threshold": (False, 250),
               "unknown_credit_review_threshold": (False, 500)}


def assumptions_problems(doc: Mapping, *, taxonomy: Mapping | None, account_names: Collection[str] | None,
                         renamed_from: Collection[str] = ()) -> list[Problem]:
    """assumptions.yaml beyond the type of its blocks: unknown keys at the top and in each block; the thresholds 0 or
    more; each cancelled subscription a mapping with a vendor; the margin APR a rate from 0 up to 1 and each retirement
    account the name of an account or a manual entry (`account_names`; not checked when None); the projections
    numbers; the targets asset classes that are no debt, each 0 to 100, with no rule on their sum (the dashboard's
    editor takes a partial set and only warns); the budgets categories of the taxonomy (or a category a rename moves,
    `renamed_from`), each 0 or more."""
    file = _ASSUMPTIONS
    out = unknown_key_problems(file, "", "", doc)
    for key, (whole, default) in _THRESHOLDS.items():
        v = doc.get(key)
        if is_number(v) and v < 0:
            unit = "business days" if whole else "dollars"
            out.append(Problem(file, key, "expected 0 or more", f"write a number of {unit}, or leave the key out (the "
                                                                f"default is {default})"))
    if isinstance(doc.get("coverage"), dict):
        out += unknown_key_problems(file, "coverage", "coverage", doc["coverage"])
    for i, e in enumerate(_items(doc.get("cancelled_subscriptions"))):
        where = f"cancelled_subscriptions[{i}]"
        if not isinstance(e, dict):
            out.append(Problem(file, where, "expected a mapping", "write `- {vendor: ...}`"))
            continue
        out += block_problems(file, "cancelled_subscriptions[]", where, e)
        if e.get("vendor") is None:
            out.append(Problem(file, f"{where}.vendor", "missing", "name the vendor as the Recurring page shows it"))
    hh = doc.get("household")
    if isinstance(hh, dict):
        out += block_problems(file, "household", "household", hh)
        apr = hh.get("margin_apr")
        if apr is not None and not (is_number(apr) and 0 <= apr < 1):
            out.append(Problem(file, "household.margin_apr", "expected a yearly rate from 0 up to 1",
                               "write it as a fraction, e.g. 0.07 for 7 %"))
        retirement = hh.get("retirement_accounts")
        for name in retirement if isinstance(retirement, dict) and account_names is not None else ():
            if name not in account_names:
                out.append(Problem(file, f"household.retirement_accounts.{name}", "no account or manual entry has "
                                   "this name", "use the name: of an account in accounts.yaml, or a manual entry's "
                                                "account:"))
    projections = doc.get("projections")
    if isinstance(projections, dict):
        out += block_problems(file, "projections", "projections", projections)
        scenarios = projections.get("return_scenarios_pct")
        if scenarios is not None and not (isinstance(scenarios, list) and all(is_number(x) for x in scenarios)):
            out.append(Problem(file, "projections.return_scenarios_pct", "expected a list of numbers",
                               "write the yearly returns in percent, e.g. [4, 6, 8]"))
        elif scenarios == []:
            # absent means the defaults; present must state a return, or the projections have none to draw
            out.append(Problem(file, "projections.return_scenarios_pct", "an empty list states no return",
                               "list at least one yearly return in percent, e.g. [4, 6, 8], or leave the key out for "
                               "the defaults"))
    targets = doc.get("targets")
    if isinstance(targets, dict):
        for cls, share in targets.items():
            where = f"targets.{cls}"
            if not (isinstance(cls, str) and cls in ASSET_CLASSES):
                out.append(Problem(file, where, f"unknown class {cls!r}", f"one of {', '.join(ASSET_CLASSES)}"))
            elif cls in LIABILITY_CLASSES:
                out.append(Problem(file, where, f"{cls!r} is a debt, and a debt is no target",
                                   "set targets for the classes you hold, and leave the debts out"))
            if not (is_number(share) and 0 <= share <= 100):
                out.append(Problem(file, where, "expected a percent from 0 to 100", "write the class's share of net "
                                                                                    "worth in percent, e.g. 40"))
    budgets = doc.get("budgets")
    if isinstance(budgets, dict):
        for category, amount in budgets.items():
            where = f"budgets.{category}"
            if taxonomy is not None and category not in taxonomy and category not in renamed_from:
                out.append(Problem(file, where, f"category {category!r} is not in the taxonomy",
                                   "use a category listed under taxonomy in category_rules.yaml"))
            if not (is_number(amount) and amount >= 0):
                out.append(Problem(file, where, "expected an amount of 0 or more",
                                   "write the monthly budget in dollars, e.g. 400"))
    return out


# ------------------------------------------------------------------------------------------------ validate

def validate(config_dir: Path | str | None = None, paths=None, *, source_dir: str | None = None,
             parser_names: Collection[str] | None = None) -> list[Problem]:
    """Every problem of the config Config would load from `config_dir` (or from `paths`, a config.Paths, when given),
    the statements folder included, sorted; [] for a valid config. Nothing is written."""
    # config imports this module, so this import waits for the call
    from .config import Config

    try:
        Config(config_dir, source_dir, paths=paths, parser_names=parser_names, check_paths=True)
    except ConfigError as e:
        return e.problems
    return []
