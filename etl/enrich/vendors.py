# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Vendor normalization: raw descriptors -> canonical vendor names.

Every descriptor pattern comes from vendor_rules.yaml: `strip_prefixes` (each removed once, from the start),
`cleanup` (each match becomes a space, in file order) and `patterns`. The engine's own cleanup is format-neutral:
whitespace runs collapse and stray separators are trimmed from both ends. A bank's export format (its ACH metadata,
its date stamps) is described in the config, never here. `acronyms` adds to ACRONYMS."""
from __future__ import annotations

import re

from ..dates import squash

# Tokens smart_title keeps upper-case when a shouty descriptor is title-cased. An explicit
# allowlist, not a length rule: THE / AND / BAR / GAS are real words (see
# test_smart_title_cases in tests/test_enrich.py for the kind of descriptor this handles).
# INC is not on it ("Acme Inc"; LLC / PLLC read as acronyms, Inc does not). National names only: a regional
# chain or a state abbreviation is a vendor_rules.yaml `acronyms:` entry.
ACRONYMS = frozenset({"ATM", "UPS", "USPS", "DMV", "IRS", "HOA", "LLC", "PLLC", "DBA",
                      "US", "USA", "AT&T", "TJ", "CVS", "KFC", "YMCA"})
# acronyms that are the acronym only as a standalone word: "US Bank", "(US)", "US," and "US/Canada" but "Zoom.Us"
# and "Med*Us", where the token regex would otherwise see the "Us" after the dot / star as the country. Only
# these characters join a word to its neighbour (whitespace and any other punctuation end it).
WHOLE_WORD_ONLY = frozenset({"US"})
WORD_JOINERS = ".*"
# a lone word that only joins two parts of a descriptor: never the last word of a fallback vendor
_SEPARATORS = frozenset({"—"})
# a token is a run of letters/digits plus & ' ’ - (so AT&T and Coca-Cola are one token each)
_TOKEN = re.compile(r"(?:[^\W_]|[&'’-])+")
# a letter right after an apostrophe that follows TWO letters (Cora's, Jo's, Mcdonald's): the
# capital str.title() adds. One letter before it is a name prefix (O'Reilly, D'Angelo, L'Occitane)
# and keeps the capital.
_AFTER_APOSTROPHE = re.compile(r"(?<=[^\W\d_][^\W\d_])(['’])([^\W\d_])")


def smart_title(s: str, acronyms: frozenset[str] = ACRONYMS) -> str:
    """str.title() for shouty bank descriptors, minus its two mistakes: no capital after an
    apostrophe that follows two or more letters (Cora's Bakery, not Cora'S Bakery; a single
    letter before it is a name prefix, so O'Reilly, D'Angelo, L'Occitane and O'Neill's stay as
    str.title() has them) and `acronyms` stay upper-case (ATM Withdrawal, Smith & Jones PLLC;
    US only as a standalone word: US Bank, (US), but Zoom.Us). Everything else — hyphens,
    digits, dots, ampersands, non-ASCII letters — is exactly str.title(), so fallback vendors do
    not churn beyond those two fixes. Mc/Mac casing (McDonald's) is out of scope by decision."""
    titled = _AFTER_APOSTROPHE.sub(lambda m: m.group(1) + m.group(2).lower(), s.title())

    def delimited(i: int) -> bool:        # index i is outside the string, or a character that ends a word
        return i < 0 or i >= len(titled) or titled[i] not in WORD_JOINERS

    def token(m: re.Match) -> str:
        up = m.group(0).upper()
        if up not in acronyms or (up in WHOLE_WORD_ONLY and not (delimited(m.start() - 1) and delimited(m.end()))):
            return m.group(0)
        return up
    return _TOKEN.sub(token, titled)


def configured_acronyms(value) -> frozenset[str]:
    """vendor_rules.yaml `acronyms`: a list of words (absent = none), compared upper-cased like the tokens."""
    if value is None:
        return frozenset()
    if not isinstance(value, list) or not all(isinstance(w, str) and w.strip() for w in value):
        raise ValueError("vendor_rules.acronyms must be a list of words, e.g. [ACH, LLP]")
    return frozenset(w.strip().upper() for w in value)


class VendorNormalizer:
    def __init__(self, vendor_rules: dict):
        self.strip_prefixes = [re.compile(p, re.IGNORECASE)
                               for p in vendor_rules.get("strip_prefixes", [])]
        self.cleanup = [re.compile(p, re.IGNORECASE)
                        for p in vendor_rules.get("cleanup", [])]
        self.acronyms = ACRONYMS | configured_acronyms(vendor_rules.get("acronyms"))
        self.patterns = [(re.compile(e["pattern"], re.IGNORECASE), e)
                         for e in vendor_rules.get("patterns", [])]

    def clean(self, raw: str) -> str:
        s = squash(raw)
        for p in self.strip_prefixes:
            s = p.sub("", s, count=1)
        for p in self.cleanup:
            s = p.sub(" ", s)
        return squash(s).strip(" -*#.,")

    def normalize(self, raw: str) -> tuple[str, dict | None]:
        """Returns (canonical_vendor, matched_rule_or_None)."""
        cleaned = self.clean(raw)
        target = cleaned.upper() or raw.upper()
        for pat, entry in self.patterns:
            if pat.search(target):
                return entry["vendor"], entry
        return self._fallback(cleaned or raw, self.acronyms), None

    @staticmethod
    def _fallback(cleaned: str, acronyms: frozenset[str] = ACRONYMS) -> str:
        words = cleaned.split()[:4]
        # a payee-first SimpleFIN descriptor is "Payee — description" (parsers/simplefin.py): the four-word cut
        # often ends on that joining dash ("New York Times —"), and a descriptor can end on one. Drop it.
        while words and words[-1] in _SEPARATORS:
            words.pop()
        name = " ".join(words)
        # Title-case shouty all-caps descriptors (apostrophe- and acronym-aware); keep mixed-case as-is
        if name.isupper():
            name = smart_title(name, acronyms)
        return name or "Unknown"
