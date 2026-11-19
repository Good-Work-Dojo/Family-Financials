# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""What reaches the provider, as properties over generated households and descriptors: for every household a seeded
generator invents (names in every case, accented, glued inside other words, with punctuation; households that state a
surname and households that state only a name holding it, some with never-send words of their own; check, phone, card
and account numbers, store numbers, digits spread through words, amounts and dates, a month beside a day; payment-app
rows in every spelling banks print, own-name transfers; with the starter rule files and with none), the request body
holds no household name token, no number of three or more digits, no amount, no date, no account id, no address and
no one a payment app names, its answer set no name-token or numbered subcategory, and the preview prints that body
byte for byte.

The checks here are written independently of etl/ai/policy.py, from the rules it states: Unicode NFKC, case-folded,
accents stripped; a token of four letters or more found inside any word, a shorter one only as a whole word or as
that word with an "s" after it (a possessive printed without its apostrophe, "BENS PIZZA"); digits
counted across spaces, dashes, dots, slashes, commas, colons and underscores. The generator's names are chosen so no
token is a word of the program's own fixed instruction text, which a test below pins. Invented people and merchants;
no network."""
from __future__ import annotations

import json
import random
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml
from conftest import ROOT, isolated_pipeline, make_txn, write_household

from etl.ai import policy
from etl.ai.candidates import candidates, withhold
from etl.ai.cli import body_for, report
from etl.ai.request import answer_set, build_request, serialise
from etl.config import Config
from etl.models import FALLBACK_CATEGORY, RESERVED_TAXONOMY, UNCATEGORIZED_SUB

SEED = 7316
HOUSEHOLDS = 36
DESCRIPTORS = 110            # per household; each written one to three times

FIRST = ["Alex", "Sam", "Robin", "Jamie", "Zoë", "José", "Søren", "Renée", "Anne-Marie", "Bo", "Jo", "Al", "Mía",
         "Noor", "Ines", "Teodor", "Priya", "Kai", "Ezra", "Lena", "Odile", "Björn", "Ng", "Maëlle", "Dmitri",
         "Łucja", "Siobhán", "Tomás", "Yusuf", "Ilse"]
SURNAMES = ["Sample", "Smith", "O'Brien", "Müller", "Nguyen", "Lindqvist", "Delacroix", "Park", "Østergaard",
            "Okafor-Lind", "Vasquez", "Kowalczyk"]
JOINT = ["Joint", "Shared", "Together", "Ours"]
# a household's own never-send words (pets, streets, schools, a landlord), and the people payment apps name
NEVER_SEND = ["Biscuit", "Elmwood", "Pemberley", "Whiskers", "Larchmont", "Quillon"]
PEOPLE = ["Quinlan Marsh", "Dorian Vale", "Ottoline Brack", "Wendeline Ruskov", "Thaddeus Crane"]
MERCHANTS = ["BLUE HERON BAKERY", "CORNER HARDWARE", "MAPLE BOOKS", "RIVERSIDE PHARMACY", "PINE STREET CAFE",
             "GREEN LEAF GROCER", "NORTHWIND FUEL", "QUIET HARBOR SPA", "TALLGRASS GENERAL STORE", "LANTERN CINEMA",
             "COPPER KETTLE DINER", "OAK & IRON GYM", "ALDI", "CAFÉ LUMIÈRE", "SUNRISE LAUNDRY", "FIELDSTONE VET",
             "BRIGHTWATER FLORIST", "HIGHLAND OUTFITTERS", "STARLING TOYS", "MOSSY ROCK GARDEN", "TIDEPOOL SUSHI",
             "GRANITE PEAK SKI", "ORCHARD MARKET", "FOXGLOVE TEA ROOM"]
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "SEPT", "OCT", "NOV", "DEC", "JUNE", "MARCH"]
ACCENTED = {"A": "Á", "E": "É", "I": "Í", "O": "Ö", "U": "Ü", "a": "à", "e": "è", "i": "ï", "o": "ô", "u": "ù"}
STARTER = ROOT / "starter"
STARTER_KIDS = yaml.safe_load((STARTER / "category_rules.yaml").read_text("utf-8"))["taxonomy"]["Kids"]


# ------------------------------------------------------------------------------------------------ the oracle

_STRIP = str.maketrans({"ø": "o", "æ": "ae", "œ": "oe", "đ": "d", "ð": "d", "ł": "l", "þ": "th", "ı": "i",
                        "ħ": "h", "ŀ": "l", "ŧ": "t"})
_SEPARATORS = set(" \t\n\r\f\v-./,:_‐‑‒–—―−")


def folded(text: str) -> str:
    s = unicodedata.normalize("NFKC", text).casefold()
    s = unicodedata.normalize("NFKD", s)
    kept = "".join(c for c in s if unicodedata.category(c) not in ("Mn", "Cf"))
    return unicodedata.normalize("NFKC", kept.translate(_STRIP))


def letter_runs(text: str) -> list[str]:
    runs, cur = [], []
    for c in folded(text):
        if c.isalpha():
            cur.append(c)
        elif cur:
            runs.append("".join(cur))
            cur = []
    if cur:
        runs.append("".join(cur))
    return runs


def tokens_of(names) -> set[str]:
    return {r for n in names for r in letter_runs(n) if len(r) >= 2}


def name_hits(text: str, tokens: set[str]) -> list[str]:
    runs = letter_runs(text)
    return sorted(t for t in tokens if (any(t in r for r in runs) if len(t) >= 4 else t in runs or t + "s" in runs))


def longest_digit_run(text: str) -> int:
    """The most digits in one stretch of `text` that holds nothing but digits and separators."""
    best = count = 0
    for c in unicodedata.normalize("NFKC", text):
        if c.isdigit():
            count += 1
            best = max(best, count)
        elif c not in _SEPARATORS:
            count = 0
    return best


_MONTH_WORDS = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
                "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov",
                "dec"}


def month_beside_day(text: str) -> bool:
    """Whether a month's name sits next to a number of one or two digits (a day), in either order."""
    parts = re.findall(r"[^\W\d_]+|\d+", folded(text))
    return any((a in _MONTH_WORDS and b.isdigit() and len(b) <= 2) or (a.isdigit() and len(a) <= 2
                                                                         and b in _MONTH_WORDS)
               for a, b in zip(parts, parts[1:], strict=False))


def strings_of(node) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for k, v in node.items() for s in (*strings_of(k), *strings_of(v))]
    if isinstance(node, list):
        return [s for v in node for s in strings_of(v)]
    return []


# ------------------------------------------------------------------------------------------------ the generator

@dataclass
class Generated:
    adults: list[str]
    children: list[str]
    surname: str
    joint: str
    accounts: dict[str, dict]
    never_send: list[str] = field(default_factory=list)
    surname_stated: bool = True       # False: household.yaml states no surname, only `name: The <surname> Family`
    rows: list = field(default_factory=list)
    secrets: set[str] = field(default_factory=set)          # every amount, date and account number written
    payment_app_rows: int = 0

    @property
    def names(self) -> list[str]:
        return [*self.adults, *self.children, self.surname, self.joint, *self.never_send]


def _case(rng: random.Random, s: str) -> str:
    return rng.choice([s.upper(), s.lower(), s.title(), s])


def _accent(rng: random.Random, s: str) -> str:
    chars = list(s)
    spots = [i for i, c in enumerate(chars) if c in ACCENTED]
    if spots:
        i = rng.choice(spots)
        chars[i] = ACCENTED[chars[i]]
    return "".join(chars)


def _fullwidth(s: str) -> str:
    return "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in s)


def _name_form(rng: random.Random, name: str) -> str:
    """A household name as a descriptor might carry it: any case, maybe accented or full-width, maybe glued to other
    letters or wrapped in punctuation, maybe a possessive with or without its apostrophe."""
    n = _case(rng, rng.choice(letter_runs(name) or [name]) if rng.random() < 0.5 else name)
    if rng.random() < 0.25:
        n = _accent(rng, n)
    if rng.random() < 0.08:
        n = _fullwidth(n)
    return rng.choice([n, f"{n}'S", f"-{n}-", f"({n})", f"{n}.", f"{n}MART", f"MC{n}", f"{n}SON", f"*{n}", f"{n}S", f"{n}s", n])


def _digits(rng: random.Random, gen: Generated, last4: str, number: str) -> str:
    amount = f"{rng.randint(100, 99999) / 100:.2f}"
    day = f"2030-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
    short = f"{rng.randint(1, 12)}/{rng.randint(10, 28)}"
    forms = [f"#{rng.randint(100, 99999)}", f"STORE {rng.randint(100, 9999):04d}", f"555 {rng.randint(0, 9999):04d}",
             f"1-800-555-{rng.randint(0, 9999):04d}", f"{rng.randint(10, 99)}.{rng.randint(10, 99)}.{rng.randint(10, 99)}",
             f"XXXX{last4}", f"CARD {last4}", f"ACCT {number}", amount, f"${amount}", day, short,
             f"{rng.randint(1, 9)},{rng.randint(100, 999)}.{rng.randint(10, 99)}", f"{rng.randint(100, 999)}",
             f"{rng.randint(1, 9)} {rng.randint(1, 9)} {rng.randint(1, 9)}", f"TERMINAL{rng.randint(100, 999)}",
             f"{rng.randint(10, 23)}:{rng.randint(10, 59)}",
             # digits spread through a word or over two words, and a day beside a month
             f"US*{rng.randint(1, 9)}K{rng.randint(1, 9)}L{rng.randint(1, 9)}", f"X{rng.randint(1, 9)} X{rng.randint(10, 99)}",
             f"#{rng.randint(10, 99)} #{rng.randint(10, 99)}", f"{rng.choice(MONTHS)} {rng.randint(1, 28)}",
             f"{rng.randint(1, 28)}{rng.choice(MONTHS)}", f"{rng.choice(MONTHS)} {rng.randint(1, 28)}TH"]
    gen.secrets |= {amount, day, short, last4, number}
    return rng.choice(forms)


def _payment(rng: random.Random, name: str, surname: str, m: str) -> str:
    """A payment-app row, naming a household member or someone else (PEOPLE), in the spellings banks print."""
    who = rng.choice(PEOPLE)
    glued = who.upper().replace(" ", "")
    return rng.choice([
        f"VENMO *{_name_form(rng, name)}", f"ZELLE TO {name.upper()} {surname.upper()}", f"CASH APP*{name.upper()} {m}",
        f"PAYPAL INST XFER {m}", f"Venmo payment {m.lower()}", f"PAYPAL *{glued}", f"PP*{glued}", f"PP *{who.upper()}",
        f"ZEL*{who.upper()}", f"ZELLEPAY {who.upper()}", f"VENMOPAYMENT {who.upper()}", f"GOOGLE PAY {who.upper()}",
        f"GOOGLE *GOOGLE PAY {who.upper()}", f"META PAY {who.upper()}", f"FACEBOOK PAY {who.upper()}",
        f"{_fullwidth('VENMO')} *{who.upper()}", f"VEN​MO *{who.upper()}", f"APPLECASH SENT {who.upper()}",
        f"SQC*{who.upper()}", f"PAYPAL *{m.replace(' ', '')}"])


def _descriptor(rng: random.Random, gen: Generated, surname: str, last4: str, number: str) -> tuple[str, bool]:
    """One descriptor and whether it is a payment-app row."""
    m = rng.choice(MERCHANTS)
    name = rng.choice(gen.names)
    kind = rng.random()
    if kind < 0.30:
        return _case(rng, m), False
    if kind < 0.55:
        n = _name_form(rng, name)
        words = m.split()
        at = rng.randint(0, len(words))
        return " ".join([*words[:at], n, *words[at:]]), False
    if kind < 0.75:
        d = _digits(rng, gen, last4, number)
        return rng.choice([f"{m} {d}", f"{d} {m}", f"{m}{d}"]), False
    if kind < 0.83:
        return _payment(rng, name, surname, m), True
    if kind < 0.90:
        return rng.choice([f"ONLINE TRANSFER TO {_name_form(rng, name)} {surname.upper()}",
                           f"{surname.upper()} {_name_form(rng, name)} PAYMENT", f"{m} FOR {_name_form(rng, surname)}"]), False
    if kind < 0.95:
        host = m.split()[0].lower()
        return rng.choice([f"{m} HELP@{host.upper()}.EXAMPLE.NET", f"WWW.{host}.EXAMPLE.NET {m}",
                           f"{m} HTTPS PAY", f"{host}.com/bill {m}"]), False
    return f"{m} {m} {m} {m}", False


def generate(rng: random.Random, i: int) -> Generated:
    pool = rng.sample(FIRST, rng.randint(2, 5))
    adults = pool[:rng.randint(1, min(2, len(pool) - 1))]
    children = pool[len(adults):]
    surname, joint = rng.choice(SURNAMES), rng.choice(JOINT)
    last4 = f"{rng.randint(1000, 9999)}"
    number = f"0000{rng.randint(10000000, 99999999)}"
    card, checking = f"demo_card_{last4}", f"demo_checking_{rng.randint(1000, 9999)}"
    gen = Generated(adults, children, surname, joint,
                    accounts={card: {"name": "Sample Card", "institution": "Example Card", "owner": adults[0],
                                     "kind": "credit-card"},
                              checking: {"name": "Sample Checking", "institution": "Example Bank", "owner": joint,
                                         "kind": "checking"}},
                    never_send=rng.sample(NEVER_SEND, rng.randint(0, 2)), surname_stated=i % 3 != 1)
    gen.secrets |= {card, checking}
    seq = 0
    for _ in range(DESCRIPTORS):
        desc, p2p = _descriptor(rng, gen, surname, last4, number)
        for _ in range(rng.choice([1, 1, 2, 2, 3])):
            cents = -rng.randint(100, 99999)
            day = f"2030-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
            gen.secrets |= {f"{-cents / 100:.2f}", day}
            account = card if rng.random() < 0.8 else checking
            gen.rows.append(make_txn(date=day, account=account, desc=desc, cents=cents, seq=seq))
            gen.payment_app_rows += p2p
            seq += 1
    return gen


def configure(tmp_path: Path, gen: Generated, i: int, rng: random.Random) -> Config:
    """The generated household's config: every other household carries the starter rule files, the rest a taxonomy
    and no rule at all; one in three states no surname, only a household name that holds it; some name never-send
    words."""
    d = tmp_path / f"h{i}" / "config"
    ai = {"accounts": rng.choice(["cards", "all"]), "min_seen": rng.choice([1, 2, 2, 3])}
    if gen.never_send:
        ai["never_send"] = gen.never_send
    named = {"surname": gen.surname} if gen.surname_stated else {"name": f"The {gen.surname} Family"}
    write_household(d, adults=gen.adults, children=gen.children, joint=gen.joint, ai=ai, **named)
    (d / "accounts.yaml").write_text(yaml.safe_dump({"accounts": gen.accounts}, allow_unicode=True), "utf-8")
    if i % 2 == 0:
        for f in ("assumptions.yaml", "category_rules.yaml", "vendor_rules.yaml", "transfer_rules.yaml",
                  "bank_category_map.yaml"):
            shutil.copy(STARTER / f, d / f)
        doc = yaml.safe_load((d / "category_rules.yaml").read_text("utf-8"))
    else:
        doc = {"taxonomy": {k: list(v) for k, v in RESERVED_TAXONOMY.items()}}
        doc["taxonomy"].update({"Dining": ["Coffee", "Restaurants"], "Pets": ["Food"]})
    # a household's own subcategories: one named after a member, possessives with and without the apostrophe, one
    # numbered, one plain, one holding a never-send word; and a category named after the family
    member = rng.choice([*gen.adults, *gen.children])
    doc["taxonomy"]["Kids"] = [*STARTER_KIDS, "Toys", "Camp 2031", f"{member} School", f"{gen.adults[0]}'s Lessons",
                               f"{gen.adults[0]}s Lessons", f"{gen.children[-1] if gen.children else member}s Club",
                               *(f"{w} Club" for w in gen.never_send)]
    doc["taxonomy"][f"{gen.surname} Rentals"] = ["Repairs"]
    (d / "category_rules.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), "utf-8")
    return Config(d)


@dataclass
class Run:
    gen: Generated
    cfg: Config
    txns: list
    body: dict
    withheld: dict
    candidate_rows: int


@pytest.fixture(scope="module")
def runs(tmp_path_factory) -> list[Run]:
    rng = random.Random(SEED)
    root = tmp_path_factory.mktemp("never-send")
    out = []
    for i in range(HOUSEHOLDS):
        gen = generate(rng, i)
        cfg = configure(root, gen, i, rng)
        p = isolated_pipeline(root / f"h{i}" / "state", gen.rows, cfg=cfg, windows={})
        p.enrich()
        body, withheld, rows = body_for(cfg, p.txns)
        out.append(Run(gen, cfg, p.txns, body, withheld, rows))
    return out


# ------------------------------------------------------------------------------------------------ the properties

def test_the_generator_makes_enough_of_every_kind_for_the_properties_to_mean_something(runs):
    descriptors = sum(len({t.description_raw for t in r.gen.rows}) for r in runs)
    assert descriptors >= 3000
    sent = sum(len(r.body["vendors"]) for r in runs)
    held = {reason: sum(r.withheld[reason] for r in runs) for reason in policy.REASONS}
    assert sent >= 300, sent
    assert held["name"] >= 300 and held["digits"] >= 300 and held["seen"] >= 50, held
    assert held["address"] >= 20, held
    assert sum(r.gen.payment_app_rows for r in runs) >= 300


def test_the_body_holds_no_household_name_token(runs):
    for r in runs:
        tokens = tokens_of(r.gen.names)
        text = serialise(r.body)
        assert name_hits(text, tokens) == [], (r.gen.names, name_hits(text, tokens))
        for s in strings_of(r.body):
            assert name_hits(s, tokens) == [], s


def test_the_body_holds_no_run_of_three_digits_and_no_amount_date_or_account(runs):
    for r in runs:
        text = serialise(r.body)
        for s in [text, *strings_of(r.body)]:
            assert longest_digit_run(s) < 3, s
        for s in strings_of(r.body):
            assert not month_beside_day(s), s
            digits = [sum(c.isdigit() for c in w) for w in re.findall(r"[^\W_]+", folded(s))]
            assert all(n < 3 for n in digits), s                                  # in one word
            assert all(not (a and b) or a + b < 3 for a, b in zip(digits, digits[1:], strict=False)), s
        for secret in r.gen.secrets:
            assert secret not in text, secret
        assert not any(mark in folded(text) for mark in ("@", "http", "www.", ".com/"))
        assert all(len(v) <= 64 for v in r.body["vendors"])


def test_no_payment_app_vendor_is_sent(runs):
    for r in runs:
        for v in r.body["vendors"]:
            assert not re.search(r"venmo|zelle|cash ?app|paypal", v), v


def test_the_answer_set_holds_no_name_token_or_numbered_name_and_only_spending_categories(runs):
    seen_kids = 0
    for r in runs:
        tokens = tokens_of(r.gen.names)
        for entry in r.body["answer_set"]:
            for name in (entry["category"], *entry["subcategories"]):
                assert name_hits(name, tokens) == [] and longest_digit_run(name) < 3, name
            assert entry["category"] not in ("Internal", "Income", "Fees & Interest")
            assert not {UNCATEGORIZED_SUB, "Venmo/Zelle Unknown"} & set(entry["subcategories"])
            assert not entry["category"].endswith(" Rentals")
            if entry["category"] == "Kids":
                seen_kids += 1
                assert set(entry["subcategories"]) == {*STARTER_KIDS, "Toys"}
    assert seen_kids >= HOUSEHOLDS // 2 - 1


def test_every_vendor_sent_is_a_candidate_seen_at_least_min_seen_times(runs):
    for r in runs:
        found = candidates(r.cfg, r.txns)
        by_key: dict[str, set[str]] = {}
        for c in found:
            by_key.setdefault(c.vendor, set()).add(c.uid)
        for v in r.body["vendors"]:
            assert len(by_key[v]) >= r.cfg.ai.min_seen
        uids = {t.uid: t for t in r.txns}
        for c in found:
            t = uids[c.uid]
            assert (t.type, t.category, t.subcategory, t.category_source) == \
                ("purchase", FALLBACK_CATEGORY, UNCATEGORIZED_SUB, "fallback")
            if r.cfg.ai.accounts == "cards":
                assert r.cfg.accounts[t.account_id]["kind"] == "credit-card"


def test_the_preview_prints_the_body_byte_for_byte(runs):
    for r in runs:
        lines: list[str] = []
        report(r.cfg, r.txns, out=lines.append)
        assert lines[-1] == serialise(r.body)
        assert json.loads(lines[-1]) == r.body


def test_only_the_vendors_and_the_answer_set_differ_between_households(runs):
    fixed = {json.dumps({k: v for k, v in r.body.items() if k not in ("vendors", "answer_set")}, sort_keys=True)
             for r in runs}
    assert len(fixed) == 1
    [only] = fixed
    # the pools stay clear of the fixed text
    assert name_hits(only, tokens_of(FIRST + SURNAMES + JOINT + NEVER_SEND + PEOPLE)) == []


def test_no_one_a_payment_app_names_is_sent(runs):
    people = tokens_of(PEOPLE)
    for r in runs:
        assert name_hits(serialise(r.body), people) == []


def test_households_that_state_only_their_name_keep_their_surname_back(runs):
    only_name = [r for r in runs if not r.gen.surname_stated]
    assert len(only_name) >= HOUSEHOLDS // 3 - 1
    for r in only_name:
        assert r.cfg.surname is None
        assert tokens_of([r.gen.surname]) <= policy.household_tokens(r.cfg)
    assert sum(r.withheld["name"] for r in only_name) >= 100
    assert sum(bool(r.gen.never_send) for r in runs) >= HOUSEHOLDS // 2


# ------------------------------------------------------------------------------------------------ vendors set upstream

def test_a_vendor_a_parser_set_whole_is_filtered_the_same_way(tmp_path):
    """No vendor cleanup at all: the generated descriptor is itself the vendor (a parser may set one), so names and
    digits at its end, beyond the first four words a fallback vendor keeps, are tested too."""
    rng = random.Random(SEED + 1)
    held_names = held_long = sent = 0
    for i in range(12):
        gen = generate(rng, i)
        gen.never_send = []                     # the config written here names none
        d = tmp_path / f"h{i}" / "config"
        named = {"surname": gen.surname} if gen.surname_stated else {"name": f"The {gen.surname} Family"}
        write_household(d, adults=gen.adults, children=gen.children, joint=gen.joint,
                        ai={"accounts": "all", "min_seen": 1}, **named)
        (d / "accounts.yaml").write_text(yaml.safe_dump({"accounts": gen.accounts}, allow_unicode=True), "utf-8")
        (d / "category_rules.yaml").write_text(yaml.safe_dump(
            {"taxonomy": {**{k: list(v) for k, v in RESERVED_TAXONOMY.items()}, "Dining": ["Coffee"]}}), "utf-8")
        cfg = Config(d)
        rows = []
        for t in gen.rows:
            t.vendor = f"{t.description_raw} {rng.choice(gen.names)}" if rng.random() < 0.1 else t.description_raw
            t.category, t.subcategory, t.category_source = FALLBACK_CATEGORY, UNCATEGORIZED_SUB, "fallback"
            rows.append(t)
        sendable, withheld = withhold(candidates(cfg, rows), cfg)
        body = build_request(sendable, answer_set(cfg), policy.CURRENT, tokens=policy.household_tokens(cfg))
        text = serialise(body)
        tokens = tokens_of(gen.names)
        assert name_hits(text, tokens) == [] and longest_digit_run(text) < 3
        assert not any(secret in text for secret in gen.secrets)
        assert all(len(v) <= 64 for v in body["vendors"])
        held_names += withheld["name"]
        held_long += withheld["length"]
        sent += len(sendable)
    assert held_names >= 100 and held_long >= 5 and sent >= 100


# ------------------------------------------------------------------------------------------------ possessives

# short names in every role a token comes from: two adults, two children, the surname, the joint label, a never-send
# word; banks print a possessive without its apostrophe ("BENS PIZZA"), so each is written that way
SHORT = {"adult": ["Ben", "Al"], "child": ["Amy", "Jo"], "surname": ["Ng"], "joint": ["Duo"], "never_send": ["Kit"]}
# words that start with a short name but are not it or its possessive: still sent
NOT_A_NAME = ["BENSON HARDWARE", "ALDI", "AMYSTIC CANDLES", "JOSTLE GAMES", "KITSCH CORNER", "DUOMO PIZZERIA"]


def _possessives(rng: random.Random, name: str) -> list[str]:
    """The name's possessive without its apostrophe as descriptors carry it: before or after a merchant, alone, in any
    case, maybe accented, full-width or wrapped in punctuation."""
    out = []
    for m in rng.sample(MERCHANTS, 8):
        word = _case(rng, f"{name}s")
        if rng.random() < 0.3:
            word = _accent(rng, word)
        if rng.random() < 0.1:
            word = _fullwidth(word)
        out.append(rng.choice([f"{word} {m}", f"{m} {word}", f"({word}) {m}", f"{word}-{m}", f"{m} BY {word}"]))
    return [*out, f"{name.upper()}S", f"{name}s", f"{name.upper()}'S {MERCHANTS[0]}"]


def _short_household(tmp_path: Path) -> Config:
    d = tmp_path / "config"
    write_household(d, adults=SHORT["adult"], children=SHORT["child"], surname=SHORT["surname"][0],
                    joint=SHORT["joint"][0], ai={"accounts": "all", "min_seen": 1, "never_send": SHORT["never_send"]})
    accounts = {"demo_card_4321": {"name": "Sample Card", "institution": "Example Card", "owner": "Ben",
                                   "kind": "credit-card"}}
    (d / "accounts.yaml").write_text(yaml.safe_dump({"accounts": accounts}), "utf-8")
    taxonomy = {k: list(v) for k, v in RESERVED_TAXONOMY.items()}
    taxonomy["Kids"] = ["Toys", *(f"{n}s Lessons" for names in SHORT.values() for n in names),
                        *(f"{n.upper()}S CLUB" for names in SHORT.values() for n in names)]
    taxonomy.update({f"{n}s Corner": ["Gifts"] for n in SHORT["adult"]})
    taxonomy["Dining"] = ["Coffee", "Bens Lessons"]
    (d / "category_rules.yaml").write_text(yaml.safe_dump({"taxonomy": taxonomy}), "utf-8")
    return Config(d)


def test_a_short_name_written_as_a_possessive_without_its_apostrophe_is_never_sent(tmp_path):
    """For every role a name token comes from (adults, children, the surname, the joint label, a never-send word), a
    short name's possessive printed without the apostrophe ("BENS PIZZA", "AMYS BAKERY") is withheld for the name in
    the vendors, and a category or subcategory named so ("Bens Lessons") is left out of the answer set, while a word
    that only starts with the name ("BENSON", "ALDI") is still sent."""
    rng = random.Random(SEED + 2)
    cfg = _short_household(tmp_path)
    tokens = policy.household_tokens(cfg)
    assert tokens_of(n for names in SHORT.values() for n in names) <= tokens
    named = [desc for names in SHORT.values() for n in names for desc in _possessives(rng, n)]
    rows = []
    for seq, desc in enumerate([*named, *NOT_A_NAME]):
        t = make_txn(date="2030-03-04", account="demo_card_4321", desc=desc, vendor=desc, seq=seq,
                     category=FALLBACK_CATEGORY, subcategory=UNCATEGORIZED_SUB)
        t.category_source = "fallback"
        rows.append(t)
    found = candidates(cfg, rows)
    assert len(found) == len(rows)
    sendable, withheld = withhold(found, cfg)
    assert sorted(sendable) == sorted({c.vendor for c in found[len(named):]})
    assert withheld["name"] == len({c.vendor for c in found[:len(named)]})
    for key in {c.vendor for c in found[:len(named)]}:
        assert policy.text_reason(key, tokens) == "name", key
        assert name_hits(key, tokens), key                           # the oracle agrees
    body = build_request(sendable, answer_set(cfg), policy.CURRENT, tokens=tokens)
    assert name_hits(serialise(body), tokens) == []
    assert {e["category"]: e["subcategories"] for e in body["answer_set"]} == {"Kids": ["Toys"], "Dining": ["Coffee"]}


@pytest.mark.parametrize("text, held", [
    ("AL'S DINER", True), ("ALS DINER", True), ("als diner", True), ("Áls Diner", True), ("DINER BY ALS", True),
    ("ALDI", False), ("ALSO DINER", False), ("DINER", False)])
def test_the_policys_own_example_holds_both_ways(text, held):
    """The module docstring's example: "AL" withholds "AL'S DINER" and, without the apostrophe, "ALS DINER", but not
    "ALDI"; a long token's possessive is inside the word already ("SMITHS HARDWARE")."""
    assert policy.holds_name(text, {"al"}) is held
    assert bool(name_hits(text, {"al"})) is held
    assert policy.holds_name("SMITHS HARDWARE", {"smith"}) and policy.holds_name("SMITH'S HARDWARE", {"smith"})
