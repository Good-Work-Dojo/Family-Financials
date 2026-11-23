# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Transfer & CC-payment dedup.

Core principle: a row an `accounts:` rule types (Phase A) never needs a counterpart to be excluded from spending —
spending is `type in (purchase, fee)`, so typing a row transfer/cc-payment/investment excludes it by construction.
Ambiguous rows (Venmo/Zelle) need a cross-account match (Phase B) to become internal; unmatched ones stay real
spending/income. With `venmo_patterns.pairing: unique` (the default) such a match is claimed only when each leg is the
other's only rival (the generic phase's count, below), so a payment out and an unrelated receipt of the same amount
on another adult's account are no transfer; `nearest` claims the best-scored, nearest match instead. A row a `kinds:`
rule types names no account of the household, so it too stays internal only when something pairs it — except a
payment a credit card received (a credit there typed `cc-payment`), which is never a refund: it stays internal, and
the generic phase may still pair it (below). Phase C explains unmatched rule-typed rows using each account's data
window.

The phases in Pipeline.enrich order: A types; B pairs the rule-typed and the person-to-person rows; every other row a
`kinds:` rule typed that B left unpaired is released (`release_unpaired`): put back as no rule had typed it, and the
pipeline runs the income and category stages over it again; the generic phase (`phase_generic`) pairs rows that no
rule and no earlier phase claimed; C explains the rest.

The generic phase needs no rule. A candidate is a row on a checking, savings or card account that nothing internal
claimed (no rule typed it, no phase paired it, it is no person-to-person row), that is not split (a split parent or
part, or a row of a vendor a split rule splits later), whose amount is at least `generic_min_amount` (assumptions.yaml,
dollars, DEFAULT_GENERIC_MIN_AMOUNT), and that nothing explained. Explained means any of: a review-inbox answer names
the row or its vendor (the family files it; answers apply later in the enrich order); the row is a purchase the
categorizer filed by anything but its fallback (a keyword, vendor or learned rule, a bank label or a parser: spending
or a refund at something the household's vocabulary names); the row is income an income rule typed; or it is income
the income identifier's fallback typed (a credit at or above its review threshold, which therefore never reached the
categorizer) that the categorizer would file if it were an ordinary purchase (`placed`, Categorizer.places). So the
only candidates are unanswered purchases the categorizer left to its fallback and unanswered fallback credits the
categorizer would leave there too, both carrying `category_source` fallback. A vendor answer names every row of its
vendor, so a vendor the family has answered for keeps that vendor's unexplained credits out of the generic matcher
too. One rule-typed row is a candidate as well: a payment a card received that a `kinds:` rule typed `cc-payment`
and phase B left unpaired (it is never released) — a transfer rule recognised it, a stronger reason than nothing
explaining it. As a card inflow it pairs only in the cash-out, card-in cell, with an ordinary candidate outflow, under
the same uniqueness; unpaired, it stays internal for phase C. Two candidates pair when their
amounts are exactly opposite, they are on different accounts, they post at most `match_window_bdays` business days
apart, the kinds of the outflow's and the inflow's accounts are a cell of GENERIC_PAIRS (cash to cash: both
`transfer`; cash out, card in: both `cc-payment`; nothing else ever), and each is the other's only rival: the only
row not already paired, of any type and on any other account, with the opposite amount inside the window. Any second
such row — a candidate or not — and neither pairs. A transfer left unpaired stays visible; a purchase called a
transfer would hide spending. `generic_matching: false` in transfer_rules.yaml turns the phase off.
A released row the generic phase does not pair either stays spending or income and goes to review (`needs_review`,
`review_reason` UNPAIRED_TRANSFER): it reads like a transfer but its other leg is not in the data.

The rules are transfer_rules.yaml: Config checks the whole file at load (`transfer_rule_problems`)
and the engine is built from the compiled rules (`compile_transfer_rules`, which runs the same
check first). A rule is scoped by `accounts:` (registry ids) or by `kinds:` (account kinds). Its
`counter_hint` says where the other leg lives, in one of these forms:

  hint              phase B: the other leg scores         phase C: accounts whose data window can make
                                                          an unmatched leg a data gap
  <account id>      +2 when it is on that account          that account
  <group name>      +1 when it is on a member              the members but the row's own account when the
                                                          group has `expect_counterpart: true`; else none
  kind:<kind>       +1 when its account has that kind      the accounts of that kind but the row's own
  bank_any          +1 when it is on a bank or card        every bank and card account
  <outside name>    nothing                                none: the far side is outside the data
  from_text:<tpl>   the template with {1}, {2}… filled from the pattern's groups, then read as one of the
                    forms above; a result that names none of them scores nothing and covers nothing

Groups (`groups:` — `{name: {accounts: [...], expect_counterpart: true|false}}`) and outside names
(`outside:` — a list) are the household's own vocabulary; nothing about account ids is inferred.
"""
from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from ..dates import business_days_between
from ..models import (
    ACCOUNT_KINDS,
    CASH_KINDS,
    CASHFLOW_KINDS,
    FALLBACK_CATEGORY,
    INCOME_CATEGORY,
    INCOME_OTHER_SUB,
    P2P_UNKNOWN_SUB,
    UNCATEGORIZED_SUB,
    OpenQuestion,
    Txn,
)

# the types a rule may give a row (models.INTERNAL_TYPES), in the order messages list them
RULE_TYPES = ("transfer", "cc-payment", "investment")
# The generic phase's fixed matrix: (the outflow's account kind, the inflow's account kind) -> the type both rows
# take. Cash to cash is a transfer, cash out with a card in is a card payment; every other pair of kinds never pairs.
GENERIC_PAIRS: Mapping[tuple[str, str], str] = MappingProxyType({
    **{(out, inn): "transfer" for out in sorted(CASH_KINDS) for inn in sorted(CASH_KINDS)},
    **{(out, "credit-card"): "cc-payment" for out in sorted(CASH_KINDS)}})
# assumptions.yaml generic_min_amount when absent: dollars; a smaller amount is never paired by the generic phase
DEFAULT_GENERIC_MIN_AMOUNT = 20.0
# the method the generic phase records in TransferResult.matches
GENERIC_METHOD = "generic"
# Txn.review_reason of a row a `kinds:` rule typed that no phase paired
UNPAIRED_TRANSFER = "unpaired-transfer"
# transfer_rules.yaml venmo_patterns.pairing: how phase B claims a pair with a person-to-person leg — only when it is
# the only possible one (`unique`), or the best-scored, nearest one (`nearest`)
P2P_PAIRINGS = ("unique", "nearest")
DEFAULT_P2P_PAIRING = "unique"
# the fields phase A records of a row before a `kinds:` rule types it: the ones it sets and the ones the income and
# category stages fill for an untyped row, which release_unpaired puts back
_BEFORE_RULE = ("type", "is_internal", "counter_hint", "needs_review", "category", "subcategory", "category_source",
                "tags", "earner", "employer")
SIGNS = ("credit", "debit")
# the hint that names every bank and card account the engine is given
BANK_ANY = "bank_any"
# the venmo_patterns keys that hold a regular expression
P2P_PATTERN_KEYS = ("debit", "credit", "zelle", "zelle_party")
_FILE = "transfer_rules.yaml"
_FORM = re.compile(r"(kind|from_text):(.+)", re.S)
# a `from_text:` template's placeholder: {1}, {2}… the pattern's groups
_PLACEHOLDER = re.compile(r"\{(\d+)\}")
_FIX_REGEX = "correct the pattern (a Python regular expression, matched with case ignored)"
_FIX_SCOPE = "scope the rule by account ids (accounts: [...]) or by account kinds (kinds: [...])"


def _form(hint: str) -> tuple[str, str] | None:
    """('kind', <kind>) or ('from_text', <template>) for a hint written in one of those forms, else None."""
    m = _FORM.fullmatch(hint)
    return (m[1], m[2]) if m else None


@dataclass(frozen=True)
class Rule:
    """One transfer_rules.yaml rule, compiled: it types a purchase row whose descriptor `pattern` finds, on an
    account in `accounts` or of a kind in `kinds` (exactly one of the two is set), with the optional `sign` and
    `bank_type` tests passed. A rule the file leaves unnamed is named `rules[<index>]`."""
    name: str
    pattern: re.Pattern
    type: str
    accounts: frozenset[str] | None
    kinds: frozenset[str] | None
    sign: str | None = None
    bank_type: str | None = None
    counter_hint: str | None = None
    needs_review: bool = False
    open_question: str | None = None

    def applies_to(self, t: Txn, kind: str | None) -> bool:
        """Does the row pass the rule's scope, bank type and sign tests (the pattern aside)? `kind` is the kind of
        the row's account."""
        if self.accounts is not None and t.account_id not in self.accounts:
            return False
        if self.kinds is not None and kind not in self.kinds:
            return False
        if self.bank_type and t.bank_type != self.bank_type:
            return False
        if self.sign == "credit" and t.amount_cents <= 0:
            return False
        return not (self.sign == "debit" and t.amount_cents >= 0)


@dataclass(frozen=True)
class Group:
    """A transfer_rules.yaml `groups:` entry: the accounts a hint of that name points at, and whether a leg left
    unmatched while one of them has data for its date is a data gap (`expect_counterpart`)."""
    accounts: tuple[str, ...]
    expect_counterpart: bool


def _regex_error(pattern) -> str | None:
    """Why `pattern` is no regular expression (compiled as the engine compiles it), or None."""
    if not isinstance(pattern, str):
        return "expected text"
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return str(e)
    return None


def _is_list_of_text(v) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) and x for x in v)


def _given(rule: dict, key: str) -> bool:
    """A scope key counts as given when it holds a non-empty value: an empty or null list scopes nothing."""
    return rule.get(key) not in (None, [])


def _block_problems(doc: dict, accounts: dict) -> tuple[list[str], set, set]:
    """The `groups:`, `outside:` and `venmo_patterns:` blocks' problems, and the group and outside names they
    define (read as far as their shape allows, so a rule naming one is not refused twice)."""
    out: list[str] = []
    outside = doc.get("outside")
    outside_names = {n for n in outside if isinstance(n, str)} if isinstance(outside, list) else set()
    if outside is not None and not _is_list_of_text(outside):
        out.append(f"{_FILE}: outside: expected a list of names — e.g. `outside: [demo_elsewhere]`")
    out.extend(f"{_FILE}: outside: '{n}' is also an account id — leave it out (an account id already names the "
               "account)" for n in sorted(outside_names) if n in accounts)
    groups = doc.get("groups")
    group_names = set(groups) if isinstance(groups, dict) else set()
    if groups is not None and not isinstance(groups, dict):
        out.append(f"{_FILE}: groups: expected a mapping — one `name: {{accounts: [...], expect_counterpart: "
                   "true|false}` entry per group")
    for name, g in groups.items() if isinstance(groups, dict) else ():
        where = f"{_FILE}: groups.{name}"
        # a key beside the two is Config's unknown-key problem (validate.transfer_rules_problems)
        if not (isinstance(g, dict) and {"accounts", "expect_counterpart"} <= set(g)
                and isinstance(g["accounts"], list) and isinstance(g["expect_counterpart"], bool)):
            out.append(f"{where}: expected {{accounts: [...], expect_counterpart: true|false}} — list the group's "
                       "account ids under accounts: and say true or false for expect_counterpart")
        else:
            out.extend(f"{where}: unknown account '{a}' — use an account id from accounts.yaml"
                       for a in g["accounts"] if not isinstance(a, str) or a not in accounts)
        if name in accounts:
            out.append(f"{where}: the name is also an account id — give the group another name")
        if name in outside_names:
            out.append(f"{where}: the name is also an outside: name — give the group another name")
        if name == BANK_ANY:
            out.append(f"{where}: the name is also {BANK_ANY} — give the group another name")
    vp = doc.get("venmo_patterns")
    if vp is not None and not isinstance(vp, dict):
        out.append(f"{_FILE}: venmo_patterns: expected a mapping — write debit:, credit: and zelle: patterns")
    elif isinstance(vp, dict):
        for key in P2P_PATTERN_KEYS:
            if vp.get(key) is not None and (error := _regex_error(vp[key])) is not None:
                out.append(f"{_FILE}: venmo_patterns.{key}: is not a valid regular expression ({error}) — "
                           f"{_FIX_REGEX}")
        party = vp.get("zelle_party")
        groups_in_party = re.compile(party).groups if party is not None and _regex_error(party) is None else 1
        if groups_in_party < 1:
            out.append(f"{_FILE}: venmo_patterns.zelle_party: has no group — put the counterparty's name in ( )")
        elif groups_in_party > 1:
            out.append(f"{_FILE}: venmo_patterns.zelle_party: has {groups_in_party} groups, and the engine reads the "
                       "first as the counterparty's name — keep one ( ) around the name and write the others as (?: )")
        if vp.get("zelle_bank_types") is not None and not _is_list_of_text(vp["zelle_bank_types"]):
            out.append(f"{_FILE}: venmo_patterns.zelle_bank_types: expected a list of bank type codes — "
                       "e.g. `zelle_bank_types: [P2P_DEBIT, P2P_CREDIT]`")
        if vp.get("pairing") is not None and vp["pairing"] not in P2P_PAIRINGS:
            out.append(f"{_FILE}: venmo_patterns.pairing: expected unique or nearest — leave it out for "
                       f"{DEFAULT_P2P_PAIRING}")
    return out, group_names, outside_names


def _is_hint(hint, accounts: dict, group_names: set, outside_names: set) -> bool:
    """Is `hint` one of the counter-hint forms (the module docstring's table)?"""
    if not isinstance(hint, str) or not hint:
        return False
    if hint in accounts or hint in group_names or hint in outside_names or hint == BANK_ANY:
        return True
    form = _form(hint)
    return form is not None and (form[0] == "from_text" or form[1] in ACCOUNT_KINDS)


def _rule_problems(i: int, r, accounts: dict, group_names: set, outside_names: set) -> list[str]:
    """One `rules:` entry's problems, each `transfer_rules.yaml: rules[i] (<name>): <what> — <fix>`."""
    named = isinstance(r, dict) and r.get("name") is not None
    where = f"{_FILE}: rules[{i}]" + (f" ({r['name']})" if named else "")
    if not isinstance(r, dict):
        return [f"{where}: expected a mapping — write `{{name: ..., pattern: ..., type: ..., accounts: [...]}}`"]
    out = []
    if r.get("pattern") in (None, ""):       # an empty pattern would match every descriptor
        out.append(f"{where}: pattern is missing — write a regular expression the descriptor is matched against")
    elif (error := _regex_error(r["pattern"])) is not None:
        out.append(f"{where}: pattern is not a valid regular expression ({error}) — {_FIX_REGEX}")
    if r.get("type") is None:
        out.append(f"{where}: type is missing — one of {', '.join(RULE_TYPES)}")
    elif r["type"] not in RULE_TYPES:
        out.append(f"{where}: unknown type '{r['type']}' — one of {', '.join(RULE_TYPES)}")
    if _given(r, "accounts") == _given(r, "kinds"):
        out.append(f"{where}: give exactly one of accounts or kinds — {_FIX_SCOPE}")
    for key, known, fix in (("accounts", accounts, "use an account id from accounts.yaml"),
                            ("kinds", ACCOUNT_KINDS, f"one of {', '.join(ACCOUNT_KINDS)}")):
        if not _given(r, key):
            continue
        if not isinstance(r[key], list):
            out.append(f"{where}: {key} is not a list — write `{key}: [...]`")
            continue
        noun = "account" if key == "accounts" else "kind"
        out.extend(f"{where}: unknown {noun} '{v}' in {key} — {fix}"
                   for v in r[key] if not isinstance(v, str) or v not in known)
    if r.get("sign") is not None and r["sign"] not in SIGNS:
        out.append(f"{where}: unknown sign '{r['sign']}' — credit or debit")
    hint = r.get("counter_hint")
    if hint is not None and not _is_hint(hint, accounts, group_names, outside_names):
        out.append(f"{where}: counter_hint '{hint}' is not an account, a group, kind:<kind>, {BANK_ANY}, an outside "
                   "name or from_text:<template> — name an account id, or list the name under groups: or outside:")
    elif (form := _form(hint) if isinstance(hint, str) else None) is not None and form[0] == "from_text" \
            and isinstance(r.get("pattern"), str) and _regex_error(r["pattern"]) is None:
        used = max((int(n) for n in _PLACEHOLDER.findall(form[1])), default=0)
        groups = re.compile(r["pattern"], re.IGNORECASE).groups
        if used > groups:
            out.append(f"{where}: counter_hint's template uses {{{used}}} but the pattern has {groups} group(s), so it "
                       "would stay unfilled — put the part the template needs in ( ) in the pattern, or use a lower "
                       "number")
    return out


def transfer_rule_problems(transfer_rules, accounts: dict) -> list[str]:
    """Every problem of a transfer_rules.yaml document against the registry `accounts` (accounts.yaml `accounts:`),
    each `transfer_rules.yaml: <key path>: <what> — <fix>`: the `outside:`, `groups:` and `venmo_patterns:` blocks
    (venmo_patterns' `cashout_income` is Config's own check), `generic_matching` (true or false; a null reads as
    absent, which is true), then each rule in file order. Empty for a valid document, and for none at all."""
    if not transfer_rules:
        return []
    if not isinstance(transfer_rules, dict):
        return [f"{_FILE}: top level: expected a mapping — write rules:, groups:, outside: and venmo_patterns: "
                "blocks"]
    out, group_names, outside_names = _block_problems(transfer_rules, accounts)
    switch = transfer_rules.get("generic_matching")
    if switch is not None and not isinstance(switch, bool):
        out.append(f"{_FILE}: generic_matching: expected true or false — true pairs exactly opposite rows no rule "
                   "claims, false leaves every pair to the rules")
    rules = transfer_rules.get("rules")
    if rules is not None and not isinstance(rules, list):
        out.append(f"{_FILE}: rules: expected a list — one `- {{name: ..., pattern: ..., ...}}` entry per rule")
    for i, r in enumerate(rules if isinstance(rules, list) else ()):
        out.extend(_rule_problems(i, r, accounts, group_names, outside_names))
    return out


def compile_transfer_rules(cfg) -> list[Rule]:
    """`cfg.transfer_rules`' rules, compiled in file order. The whole document is checked against `cfg.accounts`
    first (transfer_rule_problems, which Config also runs at load) and the first problem raises ValueError, so a
    document set after load is checked too. `cfg` is a Config, or anything with those two attributes."""
    doc = cfg.transfer_rules
    problems = transfer_rule_problems(doc, cfg.accounts)
    if problems:
        raise ValueError(problems[0])
    rules = []
    for i, r in enumerate((doc or {}).get("rules") or []):
        rules.append(Rule(
            name=str(r["name"]) if r.get("name") is not None else f"rules[{i}]",
            pattern=re.compile(r["pattern"], re.IGNORECASE),
            type=r["type"],
            accounts=frozenset(r["accounts"]) if _given(r, "accounts") else None,
            kinds=frozenset(r["kinds"]) if _given(r, "kinds") else None,
            sign=r.get("sign"),
            bank_type=r.get("bank_type") or None,
            counter_hint=r.get("counter_hint"),
            needs_review=bool(r.get("needs_review")),
            open_question=r.get("open_question") or None))
    return rules


def _optional_pattern(pattern: str | None) -> re.Pattern | None:
    return re.compile(pattern, re.IGNORECASE) if pattern else None


def _search(pattern: re.Pattern | None, text: str) -> bool:
    return pattern is not None and pattern.search(text) is not None


@dataclass
class TransferResult:
    matches: list[tuple[str, str, str, str, int]] = field(default_factory=list)
    # (group_id, out_uid, in_uid, method, bday_distance)
    open_questions: list[OpenQuestion] = field(default_factory=list)
    # the rows a `kinds:` rule typed in phase A, by uid: their _BEFORE_RULE fields as they were before the rule
    kind_typed: dict[str, dict] = field(default_factory=dict)
    # the uids release_unpaired put back
    released: set[str] = field(default_factory=set)


def _group_id(out: Txn, inn: Txn) -> str:
    """A pair's transfer_group_id: the same two rows give the same id in every run and in every phase."""
    return "TG-" + hashlib.sha1((out.uid + inn.uid).encode()).hexdigest()[:12]


# Cash & Misc subcategories that say nothing about what a P2P payment was for
_GENERIC_P2P_SUBCATS = frozenset({UNCATEGORIZED_SUB, P2P_UNKNOWN_SUB})


def _names_the_p2p_purpose(t: Txn) -> bool:
    """Does a P2P outflow's Cash & Misc label already say what the payment was?

    A learned rule or an explicit vendor rule has identified the payee (a named Zelle recipient, say) and
    stands with any Cash & Misc subcategory. A keyword rule stands only when it names a SPECIFIC Cash &
    Misc subcategory (Legal, ATM, Checks, Wire, Services, Prepaid Card): the descriptor matched something
    more telling than "a P2P payment". Every other Cash & Misc label — the generic fallback, the P2P
    catch-all keyword rule (Venmo/Zelle Unknown), a keyword rule with no or a generic subcategory, a
    bank label — gets the Venmo/Zelle Unknown treatment in `_finalize_venmo`."""
    if t.category_source in ("learned", "vendor_rule"):
        return True
    return (t.category_source == "keyword" and bool(t.subcategory)
            and t.subcategory not in _GENERIC_P2P_SUBCATS)


class TransferEngine:
    def __init__(self, rules: list[Rule], transfer_rules: dict, assumptions: dict,
                 account_windows: dict[str, tuple[str, str]],
                 bank_account_ids: set[str],
                 account_kinds: dict[str, str] | None = None,
                 household: dict | None = None,
                 split_vendors: Iterable[str] = (),
                 account_names: dict[str, str] | None = None):
        """`rules` is compile_transfer_rules' output for `transfer_rules`, whose `groups:`, `outside:`,
        `venmo_patterns:` and `generic_matching` the engine reads as they were checked at load. `bank_account_ids` are
        the bank and card accounts (the P2P tests and `bank_any`), `account_kinds` maps each registry id to its kind
        (kind-scoped rules, `kind:` hints and the generic phase; without it no account has a kind), `household`
        carries `own_name_tokens`, `split_vendors` names the casefolded vendors a split rule splits
        (Config.split_rules' keys), whose rows the generic phase leaves alone, and `account_names` maps a registry
        id to the name a gap question prints (an id without one is printed as it is)."""
        self.rules = list(rules)
        doc = transfer_rules or {}
        self.groups: dict[str, Group] = {
            name: Group(tuple(g["accounts"]), g["expect_counterpart"]) for name, g in (doc.get("groups") or {}).items()}
        self.outside: frozenset[str] = frozenset(doc.get("outside") or ())
        # Person-to-person rows: only the patterns the file gives. With no venmo_patterns block no row is one.
        vp = doc.get("venmo_patterns") or {}
        self.venmo_debit = _optional_pattern(vp.get("debit"))
        self.venmo_credit = _optional_pattern(vp.get("credit"))
        self.zelle = _optional_pattern(vp.get("zelle"))
        # a bank's own type codes for its Zelle rows, and its wording that names the counterparty (one group), read
        # from the upper-cased descriptor; either may be absent
        self.zelle_bank_types: frozenset[str] = frozenset(vp.get("zelle_bank_types") or ())
        self.zelle_party = _optional_pattern(vp.get("zelle_party"))
        # where an unmatched cash-out credit is filed: [{accounts, subcategory, earner?}], first entry listing the
        # row's account wins, else the income catch-all (validated at load: Config)
        self.cashout_income: list[dict] = list(vp.get("cashout_income") or [])
        # how phase B claims a pair with a person-to-person leg (checked at load; absent or null: the default)
        self.p2p_pairing: str = DEFAULT_P2P_PAIRING if vp.get("pairing") is None else vp["pairing"]
        self.window = assumptions.get("match_window_bdays", 3)
        self.venmo_window = assumptions.get("venmo_match_window_bdays", 5)
        self.venmo_threshold = assumptions.get("venmo_review_threshold", 200) * 100
        self.windows = account_windows
        self.bank_accounts = bank_account_ids
        self.kinds: dict[str, str] = dict(account_kinds or {})
        # the ids an account hint may name: the registry's, and any account with a data window
        self.account_ids: frozenset[str] = frozenset(self.kinds) | frozenset(account_windows)
        hh = household or {}
        # who counts as "us" on a Zelle row (Config.household)
        self.own_names: tuple[str, ...] = tuple(hh.get("own_name_tokens") or ())
        # the generic phase: on unless the file says `generic_matching: false`; its minimum in cents, from
        # assumptions.yaml generic_min_amount in dollars (both checked at load)
        self.generic_matching: bool = doc.get("generic_matching") is not False
        minimum = assumptions.get("generic_min_amount")
        self.generic_min_cents: int = round((DEFAULT_GENERIC_MIN_AMOUNT if minimum is None else minimum) * 100)
        self.split_vendors: frozenset[str] = frozenset(split_vendors)
        self.account_names: dict[str, str] = dict(account_names or {})

    @classmethod
    def from_config(cls, cfg, account_windows: dict[str, tuple[str, str]],
                    bank_account_ids: set[str]) -> TransferEngine:
        """The engine for a Config: its transfer rules compiled (so a document set after load is checked too), its
        assumptions, its accounts' kinds, its household and its split rules' vendors."""
        return cls(compile_transfer_rules(cfg), cfg.transfer_rules, cfg.assumptions, account_windows,
                   bank_account_ids, account_kinds={a: m["kind"] for a, m in cfg.accounts.items()},
                   household=cfg.household, split_vendors=cfg.split_rules,
                   account_names={a: m["name"] for a, m in cfg.accounts.items()})

    # ---------------- Phase A ----------------

    def phase_a(self, txns: list[Txn]) -> TransferResult:
        result = TransferResult()
        seen_questions: set[str] = set()
        for t in txns:
            if t.type != "purchase":   # rows a parser already typed keep their type
                continue
            kind = self.kinds.get(t.account_id)
            for rule in self.rules:
                if not rule.applies_to(t, kind):
                    continue
                m = rule.pattern.search(t.description_raw)
                if not m:
                    continue
                if rule.kinds is not None:      # stays internal only once paired: kept for release_unpaired
                    result.kind_typed[t.uid] = {f: copy.copy(getattr(t, f)) for f in _BEFORE_RULE}
                t.type = rule.type
                t.is_internal = True
                t.counter_hint = self._resolve_hint(rule.counter_hint, m)
                if rule.needs_review:
                    t.needs_review = True
                if rule.open_question and rule.name not in seen_questions:
                    seen_questions.add(rule.name)
                    result.open_questions.append(OpenQuestion(
                        topic=rule.name, question=rule.open_question,
                        context=f"e.g. {t.date} {t.description_raw[:80]}"))
                break
        return result

    @staticmethod
    def _resolve_hint(hint: str | None, m: re.Match) -> str | None:
        """The hint a matched row carries: a `from_text:` template with {1}, {2}… filled from the match's groups,
        any other hint as written."""
        form = _form(hint) if hint else None
        if form is None or form[0] != "from_text":
            return hint
        template = form[1]
        for i in range(1, (m.lastindex or 0) + 1):
            template = template.replace(f"{{{i}}}", m.group(i) or "")
        return template

    def _hint_kind(self, hint: str) -> str | None:
        """The kind a `kind:<kind>` hint names, else None."""
        form = _form(hint)
        return form[1] if form is not None and form[0] == "kind" else None

    # ---------------- Phase B ----------------

    def phase_b(self, txns: list[Txn], result: TransferResult) -> None:
        """Pair the rule-typed and the person-to-person rows. Every viable (outflow, inflow) pair is scored by its
        hints, then by its distance in business days, and claimed best-first, so a weak-hint pair cannot take an inflow
        whose counterpart with mutual hints merely sorts later. With `venmo_patterns.pairing: unique` (the default), a
        pair with a leg no rule typed (a person-to-person row) is viable only when each leg is the other's only rival
        (_rival_index, the generic phase's own count, within the pair's window), read from the rows as they stand
        before any pair is made; otherwise neither leg is paired here and each goes on as an unmatched row. A pair of
        two rule-typed rows is claimed best-first whatever the setting."""
        candidates = [t for t in txns if self._is_candidate(t)]
        inflows: dict[int, list[Txn]] = {}
        for t in candidates:
            if t.amount_cents > 0:
                inflows.setdefault(t.amount_cents, []).append(t)
        rivals = self._rival_index(txns) if self.p2p_pairing == "unique" else None
        pairs = []
        for out in (t for t in candidates if t.amount_cents < 0):
            for inn in inflows.get(-out.amount_cents, []):
                if inn.account_id == out.account_id:
                    continue
                dist = business_days_between(out.date, inn.date)
                window = max(self._window_for(out), self._window_for(inn))
                if dist > window:
                    continue
                if rivals is not None and not (out.is_internal and inn.is_internal) \
                        and not ([r.uid for r in rivals(out, window)] == [inn.uid]
                                 and [r.uid for r in rivals(inn, window)] == [out.uid]):
                    continue
                pairs.append(((-self._hint_score(out, inn), dist,
                               out.date, out.uid, inn.uid), out, inn, dist))
        matched: set[str] = set()
        for _, out, inn, dist in sorted(pairs, key=lambda p: p[0]):
            if out.uid in matched or inn.uid in matched:
                continue
            matched.add(inn.uid)
            matched.add(out.uid)
            gid = _group_id(out, inn)
            method = ("venmo_cashout" if self._is_venmoish(out) or self._is_venmoish(inn)
                      else "pair")
            for t in (out, inn):
                t.transfer_group_id = gid
                t.match_status = "matched"
                if not t.is_internal:       # venmo/zelle leg becoming internal
                    t.is_internal = True
                    t.type = "transfer"
            result.matches.append((gid, out.uid, inn.uid, method, dist))

    def _is_candidate(self, t: Txn) -> bool:
        if t.is_internal:
            return True
        return self._is_venmoish(t)

    def _is_venmoish(self, t: Txn) -> bool:
        if t.account_id not in self.bank_accounts:
            return False
        desc = t.description_raw
        if _search(self.venmo_debit, desc) or _search(self.venmo_credit, desc):
            return True
        if _search(self.zelle, desc) or t.bank_type in self.zelle_bank_types:
            # A Zelle row may name its counterparty; only own-name rows can be internal transfers — third-party
            # Zelle is real spending/income. A row whose wording names no party stays a candidate.
            party = self.zelle_party.search(desc.upper()) if self.zelle_party is not None else None
            # a token is a whole word of the party's name: an adult named Ed is not FRED SMITH
            if party and not any(re.search(rf"\b{re.escape(n)}\b", party.group(1)) for n in self.own_names):
                return False
            return True
        return False

    def _window_for(self, t: Txn) -> int:
        return self.venmo_window if self._is_venmoish(t) else self.window

    def _hint_score(self, a: Txn, b: Txn) -> int:
        """How strongly the two legs' hints point at each other, both directions summed (the module docstring's
        table): +2 for a hint naming the other leg's account, +1 for a group, kind or bank_any hint the other leg's
        account belongs to, nothing for an outside name or a hint that names nothing."""
        score = 0
        for x, y in ((a, b), (b, a)):
            hint = x.counter_hint
            if not hint:
                continue
            if hint == y.account_id:
                score += 2
            elif hint in self.groups:
                score += int(y.account_id in self.groups[hint].accounts)
            elif (kind := self._hint_kind(hint)) is not None:
                score += int(self.kinds.get(y.account_id) == kind)
            elif hint == BANK_ANY:
                score += int(y.account_id in self.bank_accounts)
        return score

    # ---------------- release and the generic phase ----------------

    def release_unpaired(self, txns: list[Txn], result: TransferResult) -> list[Txn]:
        """Put every row a `kinds:` rule typed in phase A that phase B left unpaired back as it was before the rule
        typed it (its _BEFORE_RULE fields), and return those rows, in list order. The pipeline then runs the income
        and category stages over them, which skipped them while they were internal, so each becomes the row it would
        have been had no transfer rule matched it. Never released: a row an `accounts:` rule typed, and a payment a
        card received (_payment_received) — released, it would be a positive purchase, and spending is net of those,
        so every payment would reduce the card's spending. Each stays internal: the generic phase may still pair the
        payment received (_is_generic_candidate), and phase C explains what is left."""
        released = []
        for t in txns:
            before = result.kind_typed.get(t.uid)
            if before is None or t.match_status == "matched" or self._payment_received(t):
                continue
            for name, value in before.items():
                setattr(t, name, copy.copy(value))
            result.released.add(t.uid)
            released.append(t)
        return released

    def _payment_received(self, t: Txn) -> bool:
        """Is the row a credit on a credit-card account a rule typed `cc-payment`: a payment the card received, which
        is never a refund?"""
        return t.type == "cc-payment" and t.amount_cents > 0 and self.kinds.get(t.account_id) == "credit-card"

    def phase_generic(self, txns: list[Txn], result: TransferResult, *, answered: Iterable[dict] = (),
                      placed: Callable[[Txn], bool] | None = None) -> None:
        """Pair the rows no rule and no earlier phase claimed (the module docstring's rule; skipped when
        `generic_matching` is false), then flag every released row still unpaired for review with the reason
        UNPAIRED_TRANSFER. A pair's two rows take the matrix's type, `is_internal`, one `transfer_group_id` and
        `match_status` matched, and the pair is recorded in `result.matches` with the method GENERIC_METHOD. The
        outcome depends on the rows, never on their order: every candidate's rivals are read from the rows as they
        stand before any pair is made, and two pairs can never share a row (each row is its partner's only rival).

        `answered` is the review-inbox answers (answers.valid_entries of the loaded document, applied later in the
        enrich order): a row a txn answer names by uid, or whose vendor a vendor answer names (both keyed as
        answers.apply keys them: the uid, and the vendor lower-cased), is the family's to file and never a
        candidate. `placed(row)` says whether the household's vocabulary would file the row as an ordinary purchase
        (Categorizer.places); an income row the income fallback typed is a candidate only when it would not."""
        uids = {a["txn_id"] for a in answered if a.get("scope") == "txn"}
        vendors = {str(a["vendor"]).lower() for a in answered if a.get("scope") == "vendor"}

        def explained(t: Txn) -> bool:
            return (t.uid in uids or (t.vendor or "").lower() in vendors
                    or (t.type == "income" and placed is not None and placed(t)))

        if self.generic_matching:
            self._pair_generic(txns, result, explained)
        for t in txns:
            if t.uid in result.released and t.match_status != "matched":
                t.needs_review = True
                t.review_reason = UNPAIRED_TRANSFER

    def _rival_index(self, txns: list[Txn]) -> Callable[[Txn, int], list[Txn]]:
        """`rivals(row, window)`: every row of `txns` that could be the row's other leg — not already paired, of any
        type and on any other account, with the opposite amount, at most `window` business days from it — as the rows
        stand when the index is built. A row that cannot pair itself (a categorized purchase, an answered row, a credit
        an income rule or the household's vocabulary names, a rule-typed leg left unpaired, a payment-app row, a
        brokerage row) still makes a pair ambiguous. The generic phase and phase B's `unique` pairing both read it."""
        open_rows: dict[int, list[Txn]] = {}
        for t in txns:
            if t.match_status != "matched" and t.transfer_group_id is None and t.amount_cents:
                open_rows.setdefault(t.amount_cents, []).append(t)

        def rivals(t: Txn, window: int) -> list[Txn]:
            return [r for r in open_rows.get(-t.amount_cents, ())
                    if r.account_id != t.account_id and business_days_between(t.date, r.date) <= window]

        return rivals

    def _pair_generic(self, txns: list[Txn], result: TransferResult, explained: Callable[[Txn], bool]) -> None:
        candidates = sorted((t for t in txns if self._is_generic_candidate(t, result) and not explained(t)),
                            key=lambda t: (t.date, t.account_id, t.amount_cents, t.uid))
        uids = {t.uid for t in candidates}
        rivals = self._rival_index(txns)
        for out in (t for t in candidates if t.amount_cents < 0):
            mine = rivals(out, self.window)
            if len(mine) != 1 or mine[0].uid not in uids:
                continue
            inn = mine[0]
            typ = self._generic_type(out, inn)
            if typ is None or len(rivals(inn, self.window)) != 1:
                continue
            dist = business_days_between(out.date, inn.date)
            gid = _group_id(out, inn)
            for t in (out, inn):
                t.type = typ
                t.is_internal = True
                t.transfer_group_id = gid
                t.match_status = "matched"
            result.matches.append((gid, out.uid, inn.uid, GENERIC_METHOD, dist))

    def _is_generic_candidate(self, t: Txn, result: TransferResult) -> bool:
        """Is the row one the generic phase may pair (the module docstring's candidate)? `result.kind_typed` names the
        rows a `kinds:` rule typed."""
        if self.kinds.get(t.account_id) not in CASHFLOW_KINDS:
            return False
        if t.transfer_group_id is not None or t.match_status == "matched":
            return False
        if t.is_parent or t.parent_id is not None or (t.vendor and t.vendor.casefold() in self.split_vendors):
            return False
        if t.amount_cents == 0 or abs(t.amount_cents) < self.generic_min_cents:
            return False
        # a payment a card received that a `kinds:` rule typed and phase B left unpaired (never released): a transfer
        # rule recognised it, so it is a candidate though it is internal; as a card inflow, GENERIC_PAIRS lets it pair
        # only with a cash outflow
        if t.uid in result.kind_typed and self._payment_received(t):
            return True
        if t.is_internal:
            return False
        # only a row nothing filed: a purchase the categorizer left to its fallback, or a credit the income
        # identifier's fallback typed (phase_generic also leaves out answered rows and the credits the categorizer
        # would file); a row a rule, a bank label, a learned rule or a parser filed is spending, a refund or income
        # the household's own vocabulary already names
        if t.type not in ("purchase", "income") or t.category_source != "fallback":
            return False
        return not self._is_venmoish(t)

    def _generic_type(self, out: Txn, inn: Txn) -> str | None:
        """The type a generic pair of this outflow and inflow takes (GENERIC_PAIRS), or None when they never pair."""
        if out.account_id == inn.account_id:
            return None
        return GENERIC_PAIRS.get((self.kinds.get(out.account_id), self.kinds.get(inn.account_id)))

    # ---------------- Phase C ----------------

    def phase_c(self, txns: list[Txn], result: TransferResult) -> None:
        unexpected: dict[tuple[str, str], list[Txn]] = {}
        for t in txns:
            if not t.is_internal or t.match_status == "matched":
                continue
            counters = self._counter_accounts(t)
            if counters and any(self._covers(c, t.date) for c in counters):
                t.match_status = "unmatched_unexpected"
                key = (t.account_id, t.counter_hint or "?")
                unexpected.setdefault(key, []).append(t)
            else:
                t.match_status = "unmatched_window"
        # One aggregated question per (account, counterparty) group — these rows
        # stay internal (deterministically typed), so this is a data-gap note,
        # not a per-row review queue.
        for (account_id, hint), rows in sorted(unexpected.items()):
            total = sum(r.amount_cents for r in rows)
            dates = sorted(r.date for r in rows)
            one = len(rows) == 1
            result.open_questions.append(OpenQuestion(
                topic="unmatched-internal",
                question=(f"{len(rows)} internal row{'' if one else 's'} in {self._named(account_id)} "
                          f"{'has' if one else 'have'} no matching row in {self._counterpart_words(hint)}, though "
                          f"the data there covers {'its date' if one else 'their dates'}: likely that export omits "
                          f"{'it. It stays' if one else 'them. They stay'} out of spending."),
                context=f"{dates[0]} → {dates[-1]}, net {total / 100:,.2f}"))
        self._finalize_venmo(txns, result)

    def _counter_accounts(self, t: Txn) -> list[str]:
        """The accounts whose data window decides whether an unmatched leg is a data gap (the module docstring's
        table); none for no hint, an outside name, a group that expects no counterpart and a hint that names
        nothing the engine knows."""
        hint = t.counter_hint
        if not hint or hint in self.outside:
            return []
        if hint in self.groups:
            group = self.groups[hint]
            return [a for a in group.accounts if a != t.account_id] if group.expect_counterpart else []
        kind = self._hint_kind(hint)
        if kind is not None:
            return sorted(a for a, k in self.kinds.items() if k == kind and a != t.account_id)
        if hint == BANK_ANY:
            return sorted(a for a in self.bank_accounts if a != t.account_id)
        return [hint] if hint in self.account_ids else []

    def _named(self, account_id: str) -> str:
        return self.account_names.get(account_id, account_id)

    def _counterpart_words(self, hint: str) -> str:
        """Where a gap question says the other leg should be, in words: an account by its name, `bank_any` as another
        bank account, a `kind:` hint as an account of that kind, a group by its name"""
        if hint == BANK_ANY:
            return "another bank account"
        kind = self._hint_kind(hint)
        if kind is not None:
            return f"a {kind} account"
        if hint in self.groups:
            return f"the accounts of the group {hint}"
        return self._named(hint)

    def _covers(self, account_id: str, date: str) -> bool:
        w = self.windows.get(account_id)
        if not w or not w[0]:
            return False
        return w[0] <= date <= w[1]

    def _finalize_venmo(self, txns: list[Txn], result: TransferResult) -> None:
        for t in txns:
            if t.is_internal or not self._is_venmoish(t) or t.match_status == "matched":
                continue
            if t.amount_cents < 0:
                t.type = "purchase"
                # an unlabelled or generic Cash & Misc P2P outflow becomes Venmo/Zelle Unknown (+ review at
                # the threshold); a label that names the purpose keeps its category, provenance and review flag
                if not t.category or (t.category == FALLBACK_CATEGORY and not _names_the_p2p_purpose(t)):
                    t.category = FALLBACK_CATEGORY
                    t.subcategory = P2P_UNKNOWN_SUB
                    t.category_source = "fallback"
                    if abs(t.amount_cents) >= self.venmo_threshold:
                        t.needs_review = True
            else:
                if _search(self.venmo_credit, t.description_raw):
                    t.type = "income"
                    t.category = INCOME_CATEGORY
                    t.category_source = "type_default"
                    # the subcategory (and the earner, when the entry names one) of the first cashout_income entry
                    # listing the row's account; elsewhere the income catch-all. No income rule typed the row as
                    # filed, so it carries no employer and starts no income phase.
                    entry = next((e for e in self.cashout_income if t.account_id in e["accounts"]), None)
                    t.subcategory = entry["subcategory"] if entry else INCOME_OTHER_SUB
                    if entry and entry.get("earner"):
                        t.earner = entry["earner"]
                    t.employer = None
                    t.needs_review = True
                elif t.amount_cents >= self.venmo_threshold:
                    t.needs_review = True   # stays positive purchase (reimbursement-ish)

