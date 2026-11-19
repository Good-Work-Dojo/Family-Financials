# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Core dataclasses shared by all parsers."""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

# The single source of truth for the app's core invariant (see CLAUDE.md):
#   spending = purchase + fee · income = income + dividend + interest ·
#   internal = transfer / cc-payment / investment (excluded from both)
# db.py's CHECK constraint mirrors TXN_TYPES as literal SQL — keep them in sync.
TXN_TYPES = ("purchase", "income", "transfer", "cc-payment", "investment",
             "dividend", "fee", "interest")
SPEND_TYPES = frozenset({"purchase", "fee"})
INCOME_TYPES = frozenset({"income", "dividend", "interest"})
INTERNAL_TYPES = frozenset({"transfer", "cc-payment", "investment"})

ASSET_CLASSES = ("Cash", "Retirement", "Stocks/ETF", "Crypto", "Precious Metals", "Real Estate/Venture",
                 "House", "Car", "529", "Private Stock", "Mortgage", "Margin",
                 "Other Debt")
# the classes that are debts (their rows are negative), and the classes whose rows are not liquid
LIABILITY_CLASSES = frozenset({"Mortgage", "Margin", "Other Debt"})
ILLIQUID_CLASSES = frozenset({"Retirement", "529"})

# accounts.yaml `kind`: a closed set, checked at load
ACCOUNT_KINDS = ("checking", "savings", "credit-card", "brokerage", "loan")
CASH_KINDS = frozenset({"checking", "savings"})
LIABILITY_KINDS = frozenset({"credit-card", "loan"})
# the accounts whose rows are day-to-day money: the transfer engine's bank set, ledger-drift eligibility and the
# cash-flow export's accounts
CASHFLOW_KINDS = frozenset({"checking", "savings", "credit-card"})
# the class an account's rows take when it declares none (a `mortgage: true` loan takes "Mortgage" instead)
DEFAULT_CLASS_BY_KIND: Mapping[str, str] = MappingProxyType({
    "checking": "Cash", "savings": "Cash", "brokerage": "Stocks/ETF", "loan": "Other Debt",
    "credit-card": "Other Debt"})


def account_asset_class(meta: Mapping) -> str:
    """The class an account's rows take, from its accounts.yaml entry: its `asset_class`, else "Mortgage" for a
    `mortgage: true` loan, else its kind's default (DEFAULT_CLASS_BY_KIND). Config.asset_class_of reads it by registry
    id; the feed parser classes holdings with it. A kind outside ACCOUNT_KINDS raises KeyError (Config refuses one at
    load)."""
    declared = meta.get("asset_class")
    if declared:
        return declared
    if meta.get("kind") == "loan" and meta.get("mortgage"):
        return "Mortgage"
    return DEFAULT_CLASS_BY_KIND[meta.get("kind")]

# The category and subcategory names the engine itself files rows under, by role. A household's taxonomy
# (category_rules.yaml `taxonomy:`) must hold every one, each subcategory under its category, and no category rename
# may move one (Config refuses either at load); a household with no taxonomy gets exactly these (RESERVED_TAXONOMY).
# Exported as assumptions.json `reserved`, so the dashboard reads them from the data. Each is also a constant below.
RESERVED: Mapping[str, str] = MappingProxyType({
    "fallback_category": "Cash & Misc",         # a purchase no rule places, and a P2P payment no rule names
    "uncategorized_sub": "Uncategorized",       # under the fallback category: the purchase no rule places
    "p2p_unknown_sub": "Venmo/Zelle Unknown",   # under the fallback category: the P2P payment no rule names
    "internal_category": "Internal",            # money moving between the household's own accounts (subcategory = type)
    "income_category": "Income",                # income, dividend and interest rows
    "income_other_sub": "Other",                # under the income category: a deposit no income rule recognises
    "fees_category": "Fees & Interest",         # fee rows
})
FALLBACK_CATEGORY = RESERVED["fallback_category"]
UNCATEGORIZED_SUB = RESERVED["uncategorized_sub"]
P2P_UNKNOWN_SUB = RESERVED["p2p_unknown_sub"]
INTERNAL_CATEGORY = RESERVED["internal_category"]
INCOME_CATEGORY = RESERVED["income_category"]
INCOME_OTHER_SUB = RESERVED["income_other_sub"]
FEES_CATEGORY = RESERVED["fees_category"]
# each reserved category with the reserved subcategories filed under it: what a taxonomy must hold, and the whole
# taxonomy of a household that writes none
RESERVED_TAXONOMY: Mapping[str, tuple[str, ...]] = MappingProxyType({
    FALLBACK_CATEGORY: (UNCATEGORIZED_SUB, P2P_UNKNOWN_SUB),
    INTERNAL_CATEGORY: (),
    INCOME_CATEGORY: (INCOME_OTHER_SUB,),
    FEES_CATEGORY: (),
})

# Where a transaction's category came from — a closed set, exported on every row as
# `category_source` so the app can badge provenance. Precedence inside the Categorizer is
# parser → vendor_rule → learned (etl/enrich/learned.py) → keyword → bank_label → fallback;
# answers and overrides re-stamp afterwards; "internal" and "type_default" are type-driven;
# "split" marks a child of a rule-driven split (etl/enrich/splits.py) — its parent keeps the
# source it had; "ai" is a fallback row the AI step's stored answer filed (etl/enrich/ai.py), which
# answers and overrides re-stamp like any other.
CATEGORY_SOURCES = ("internal", "type_default", "parser", "vendor_rule", "learned",
                    "keyword", "bank_label", "fallback", "answer", "override", "split", "ai")

# household.yaml `modules`: the optional parts of the dashboard a household may switch on or off, by id. An id outside
# this set is refused at load; assumptions.json `modules` carries the household's own switches as it states them.
MODULE_IDS = ("home_buying",)


@dataclass
class Txn:
    date: str                 # ISO YYYY-MM-DD
    account_id: str           # registry key, e.g. demo_checking_0000
    description_raw: str
    amount_cents: int         # signed; negative = outflow
    source_file: str
    bank_type: str | None = None      # institution's own type column (ACH_DEBIT, Sale, ...)
    bank_category: str | None = None  # the card issuer's own Category column
    # enrichment fields (filled by enrich/*)
    type: str = "purchase"
    vendor: str | None = None
    category: str | None = None
    subcategory: str | None = None
    category_source: str | None = None   # one of CATEGORY_SOURCES once enriched
    tags: list[str] = field(default_factory=list)
    is_internal: bool = False
    transfer_group_id: str | None = None
    match_status: str | None = None
    counter_hint: str | None = None
    needs_review: bool = False
    earner: str | None = None  # who earned an income row (from income_rules earner:)
    # A family fact (config override or review-inbox answer) touched this row
    # (transient, not persisted): settled rows stay out of top_unmapped_vendors.
    family_fact: bool = False
    seq: int = 0              # row index within source file (uid disambiguator)
    # SimpleFIN's own transaction id (transient, not persisted): the cross-snapshot
    # dedup key. Descriptors mutate between snapshots (pending→posted masking), so
    # content-hash uids alone would double-count the same feed transaction.
    sf_tid: str | None = None
    # Rule-driven splits (etl/enrich/splits.py). A PARENT keeps its own amount and its
    # (display-only) category and is excluded from every sum; its CHILDREN carry the amounts and
    # the categories and name the parent by uid. `split_index` is 1-based on a child and 0 on every
    # other row; it makes a child's uid (below), so two equal legs (a 50/50 split) stay distinct.
    parent_id: str | None = None
    is_parent: bool = False
    split_index: int = 0
    # TRANSIENT (like family_fact and sf_tid: no exporter, DB column or hash reads it, and equality and repr
    # ignore it): the review flag a split parent had before splits.apply cleared it, so splits.unsplit_retyped
    # can give it back when an override retypes the parent out of spending and it is an ordinary row again
    # (overrides.apply_set drops it whenever an answer or override sets needs_review explicitly).
    prior_needs_review: bool | None = field(default=None, compare=False, repr=False)
    # TRANSIENT (like prior_needs_review): the `employer:` label of the income rule that typed this row
    # (enrich/income.py IncomeIdentifier); detect_income_phases groups by it. Every other path that types a row as
    # income leaves it None.
    employer: str | None = field(default=None, compare=False, repr=False)
    # TRANSIENT (like employer): why a stage sent the row to review — "unpaired-transfer" for a row a `kinds:` transfer
    # rule typed that no phase paired (enrich/transfers.py TransferEngine.phase_generic), "uncategorized" for a
    # purchase the categorizer's fallback flagged (enrich/categorize.py UNCATEGORIZED_REASON)
    review_reason: str | None = field(default=None, compare=False, repr=False)

    @property
    def uid(self) -> str:
        if self.split_index:
            # A child hashes its PARENT's uid and its index and never its own amount: two
            # same-day, same-descriptor purchases are told apart by the parent's seq, so
            # their children cannot collide on the txn_uid primary key, and re-tuning a leg's
            # percentage keeps the part's uid (and with it any answer stored against it).
            if self.parent_id is None:
                raise ValueError(f"split_index {self.split_index} needs a parent_id "
                                 "(a child's uid derives from its parent's)")
            key = f"{self.parent_id}|s{self.split_index}"
        elif self.parent_id is not None:
            # the reverse mistake: a child that lost its index would hash like an ordinary row
            # (and could collide with the real one it was copied from)
            raise ValueError(f"parent_id {self.parent_id!r} needs a split_index >= 1 "
                             "(a child without one would hash as an ordinary row)")
        else:
            # Every ordinary row and every parent: byte-identical to the pre-split hash, so no
            # stored txn answer and no transfer_matches row is orphaned.
            key = f"{self.account_id}|{self.date}|{self.amount_cents}|{self.description_raw}|{self.seq}"
        return "tx_" + hashlib.sha1(key.encode()).hexdigest()[:16]


@dataclass
class AssetRow:
    snapshot_date: str        # date this value belongs to in the time series
    account: str              # display name
    institution: str
    owner: str                # one of Config.owners (household.yaml)
    asset_class: str
    value_cents: int          # negative for Mortgage/Margin
    liquid: bool
    # positions|bank_balance|simplefin_balance|stated (stated = a `manual_entries` balance
    # the family entered). simplefin_balance marks the balance row of a brokerage, loan or
    # card (and a margin account's remainder row): its balance on or before the snapshot
    # date, whether the feed reported it in its newest file, it is carried, or it came from a
    # statement file. The app groups exactly that source (app/src/lib/holdings.ts
    # LIVE_BALANCE_SOURCES, used by Investments and Planning), so a new source for such rows
    # must be added there too or its accounts vanish from both.
    # bank_balance is NOT in that set: it marks a checking or savings account's balance row
    # (from a statement file or the feed), which the Investments page must not book.
    source: str
    as_of: str | None = None  # underlying statement date when != snapshot_date
    as_of_assumed: bool = False   # the date is assumed, not reported (exported; no row builder sets it)
    needs_review: bool = False    # always False: no row builder sets it; kept, like as_of_assumed, so the row's
                                  # shape in finance.db, assets_timeseries.json and the app's schema does not change
    account_id: str | None = None  # registry key when feed-sourced (join key for per-account views)
    manual_id: str | None = None   # the manual asset (etl/manual_assets.py) a `stated` row is; None for an account's
    carried_back: bool = False     # a manual asset before its first value, at that value (exported; not in finance.db)


@dataclass
class Holding:
    account: str
    institution: str
    owner: str
    symbol: str | None
    name: str
    asset_class: str
    quantity: float | None
    value_cents: int
    cost_basis_cents: int | None
    as_of: str
    liquid: bool = True
    notes: str | None = None
    account_id: str | None = None  # registry key when SimpleFIN-sourced (join key for per-account views)


@dataclass
class BalanceObs:
    """One account-balance observation on a date — from a SimpleFIN snapshot or a
    CSV-derived latest balance (see `source` below). The union over time = the balance
    timeseries."""
    account_id: str
    as_of: str                 # ISO YYYY-MM-DD
    balance_cents: int         # signed as the institution reports (cards negative); a loan's is stored negative
                               # whatever sign the feed uses
    available_cents: int | None = None
    limit_cents: int | None = None
    source: str = "simplefin"  # simplefin|csv


@dataclass
class FileStats:
    source_file: str
    parser: str
    rows_total: int = 0
    rows_parsed: int = 0
    rows_skipped: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)
    duplicates_removed: int = 0
    date_min: str | None = None
    date_max: str | None = None
    sum_cents: int = 0        # parsed signed total, for reconciliation
    # the registry account a statement file was read for, set by the pipeline; None for a bank-feed pull, which
    # reports many accounts
    account: str | None = None

    def skip(self, reason: str, n: int = 1) -> None:
        self.rows_skipped += n
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + n

    def observe(self, date: str, amount_cents: int) -> None:
        self.rows_parsed += 1
        self.sum_cents += amount_cents
        if self.date_min is None or date < self.date_min:
            self.date_min = date
        if self.date_max is None or date > self.date_max:
            self.date_max = date


@dataclass
class OpenQuestion:
    topic: str
    question: str
    context: str = ""


@dataclass
class QualityCheck:
    name: str
    status: str               # pass|warn|fail|info
    detail: str
