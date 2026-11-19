# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""etl/manual_assets.py and how net worth reads it: the store's document of the assets entered in the app, merged with
accounts.yaml `manual_entries` into the effective list; each asset valued on a day by its dated values (the last one
dated on or before the day, the first carried back before it, nothing before the day it was acquired); and the list
exported. Invented assets and accounts only."""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import threading
from pathlib import Path

import pytest
import yaml
from conftest import ROOT, isolate, write_household

from etl import exports, pipeline
from etl import manual_assets as ma
from etl import networth as nw
from etl.config import Config
from etl.models import ASSET_CLASSES, LIABILITY_CLASSES, BalanceObs
from etl.pipeline import Pipeline

OWNERS = ("Alex", "Sam", "Joint")
ACCOUNT_NAMES = ("Sample Checking", "Sample Brokerage")
CHECKING = {"name": "Sample Checking", "institution": "Sample Bank", "owner": "Alex", "kind": "checking"}
BROKERAGE = {"name": "Sample Brokerage", "institution": "Sample Broker", "owner": "Sam", "kind": "brokerage"}
# config entries as Config hands them on (dates as ISO text)
HOME = {"account": "Family home", "institution": "Sample Assessor", "owner": "Joint", "asset_class": "House",
        "value": 400_000, "as_of": "2030-01-01"}
SEDAN = {"account": "Sedan", "institution": "Sample Dealer", "owner": "Sam", "asset_class": "Car", "value": 20_000,
         "as_of": "2030-01-01"}


def app(asset_id: str = "family-home", *, name: str = "Family home", values=(("2030-01-01", 450_000),),
        **over) -> dict:
    """An asset as the store keeps it in the document."""
    return {"id": asset_id, "name": name, "owner": "Joint", "asset_class": "House", "liquid": False,
            "values": [{"as_of": d, "value": v} for d, v in values], "updated_at": "2030-02-01T00:00:00Z", **over}


def doc(*assets) -> dict:
    return {"version": 1, "updated_at": "2030-02-01T00:00:00Z", "assets": list(assets)}


def merged(document: dict, entries=(HOME,)) -> tuple[list[ma.ManualAsset], list]:
    return ma.effective(document, list(entries), OWNERS, ASSET_CLASSES, LIABILITY_CLASSES, ACCOUNT_NAMES)


def _day(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


def _obs(account_id: str, day: str, cents: int) -> BalanceObs:
    return BalanceObs(account_id=account_id, as_of=day, balance_cents=cents, source="simplefin")


def _series(assets: list[ma.ManualAsset], obs: list[BalanceObs], accounts: dict | None = None) -> nw.SeriesMap:
    accounts = {"demo_checking_0000": CHECKING} if accounts is None else accounts
    return nw.build_series(obs, accounts, assets, include_credit_cards=False)


def _one(document: dict) -> ma.ManualAsset:
    (asset,), checks = merged(document, ())
    assert checks == []
    return asset


def _row(snap: nw.Snapshot, row_id: str = "manual:family-home") -> nw.Row | None:
    return next((r for r in snap.rows if r.row_id == row_id), None)


# a checking account reporting at every month end from January to May, then mid-June
_MONTHLY = [_obs("demo_checking_0000", d, c) for d, c in (
    ("2030-01-31", 1_000_00), ("2030-02-28", 1_100_00), ("2030-03-31", 1_200_00), ("2030-04-30", 1_300_00),
    ("2030-05-31", 1_400_00), ("2030-06-12", 1_500_00))]


# ------------------------------------------------------------------ the effective list

def test_a_missing_document_is_the_config_entries_alone(tmp_path):
    assert ma.load(tmp_path / "manual_assets.json") == ({"assets": []}, [])
    assets, checks = merged({"assets": []}, (SEDAN, {**HOME, "acquired": "2029-12-01", "liquid": False}))
    assert checks == []
    assert assets == [
        ma.ManualAsset(id="family-home", name="Family home", institution="Sample Assessor", owner="Joint",
                       asset_class="House", liquid=False, acquired=_day("2029-12-01"),
                       values=((_day("2030-01-01"), 40_000_000),), origin="config"),
        ma.ManualAsset(id="sedan", name="Sedan", institution="Sample Dealer", owner="Sam", asset_class="Car",
                       liquid=False, acquired=None, values=((_day("2030-01-01"), 2_000_000),), origin="config")]
    # a config entry's id is its row id's slug, so the rows the snapshots carried before keep their ids
    assert [nw.MANUAL_PREFIX + a.id for a in assets] == [nw.manual_row_id(e["account"]) for e in (HOME, SEDAN)]


def test_an_app_asset_overrides_the_config_entry_with_its_id_and_removing_it_brings_the_entry_back():
    (asset,), checks = merged(doc(app(values=(("2030-03-01", 450_000),))))
    assert checks == [] and (asset.id, asset.origin, asset.institution) == ("family-home", "app", None)
    assert asset.values == ((_day("2030-03-01"), 45_000_000),)
    for gone in (doc(app(deleted_at="2030-03-02T00:00:00Z")), doc()):
        (back,), checks = merged(gone)
        assert checks == [] and (back.id, back.origin, back.values) == (
            "family-home", "config", ((_day("2030-01-01"), 40_000_000),))
    # a tombstone written as null is no tombstone: the asset is live
    (live,), _ = merged(doc(app(deleted_at=None)))
    assert live.origin == "app"
    # an app asset of its own id sits beside the config entry, both counted, sorted by name then id
    assets, _ = merged(doc(app("boat", name="boat", asset_class="Other Debt", values=(("2030-01-01", -5_000),)),
                           app("canoe", name="Canoe")), (HOME, SEDAN))
    assert [(a.id, a.origin) for a in assets] == [
        ("boat", "app"), ("canoe", "app"), ("family-home", "config"), ("sedan", "config")]


def test_the_entries_an_app_asset_hides_are_listed_and_a_removed_override_hides_none():
    """manual_assets.overridden: the config entries whose id an app asset of the effective list carries, as the config
    states them; a tombstone, an override left out or no document at all hides nothing, and the entry is counted."""
    assets, _ = merged(doc(app(values=(("2030-03-01", 450_000),))), (HOME, SEDAN))
    assert [(a.id, a.origin, a.institution, a.values) for a in ma.overridden(assets, [HOME, SEDAN])] == [
        ("family-home", "config", "Sample Assessor", ((_day("2030-01-01"), 40_000_000),))]
    removed = doc(app(deleted_at="2030-03-02T00:00:00Z"))
    left_out = doc(app(owner="Nobody"))
    for document in (removed, left_out, {"assets": []}):
        assets, _ = merged(document, (HOME, SEDAN))
        assert ma.overridden(assets, [HOME, SEDAN]) == []
        assert ("family-home", "config") in [(a.id, a.origin) for a in assets]
    # an app asset of its own id hides nothing
    assets, _ = merged(doc(app("canoe", name="Canoe")), (HOME,))
    assert ma.overridden(assets, [HOME]) == []


def test_a_counted_asset_in_a_debt_class_stating_a_value_above_zero_warns_and_still_counts():
    """Net worth adds a manual value as stated, so a debt written as a positive figure would raise it: the engine says
    so in one warning per asset (never a value), from the app or the config, and counts it unchanged."""
    loan = {"account": "Family loan", "institution": "Sample Lender", "owner": "Joint", "asset_class": "Other Debt",
            "value": 5_000, "as_of": "2030-01-01"}
    assets, checks = merged(doc(app("boat", name="Boat", asset_class="Margin",
                                    values=(("2030-01-01", -1_000), ("2030-02-01", 2_000))),
                                app("bike", name="Bike", asset_class="Other Debt", values=(("2030-01-01", -300),))),
                            (loan,))
    assert [(c.name, c.status, c.detail) for c in checks] == [
        ("manual_assets.debt_sign", "warn", "manual asset 'boat' from the app is in the debt class Margin but states a "
         "value above zero, which net worth counts as an asset: a debt's value is negative (in the dashboard, enter the "
         "amount owed)"),
        ("manual_assets.debt_sign", "warn", "manual asset 'family-loan' in accounts.yaml is in the debt class Other Debt "
         "but states a value above zero, which net worth counts as an asset: a debt's value is negative (in the "
         "dashboard, enter the amount owed)")]
    assert {a.id: a.values[-1][1] for a in assets} == {"bike": -30_000, "boat": 200_000, "family-loan": 500_000}


def test_an_app_asset_with_a_non_ascii_id_overrides_the_config_entry_of_that_slug():
    """A config entry's id keeps its name's letters whatever their script, so the id rule takes every such id and an
    app asset carrying it overrides the entry."""
    chalet = {**HOME, "account": "Résidence d'Été"}
    slug = nw.manual_slug(chalet["account"])
    assert slug == "résidence-d-été" and ma.is_id(slug)
    (asset,), checks = merged(doc(app(slug, name="Résidence d'Été", values=(("2030-03-01", 500_000),))), (chalet,))
    assert checks == [] and (asset.id, asset.origin, asset.values) == (slug, "app", ((_day("2030-03-01"), 50_000_000),))
    # the rule: runs of letters or digits joined by single dashes, its own lowercase, at most 64 characters
    for good in ("a", "family-home", "2nd-car", "résidence", "日本-2", "a" * 64):
        assert ma.is_id(good), good
    for bad in ("", "-a", "a-", "a--b", "a_b", "a b", "A", "Résidence", "a" * 65, "a\n", 7, None):
        assert not ma.is_id(bad), bad


def test_a_value_at_the_edge_of_the_range_counts():
    assert nw.MAX_MANUAL_VALUE == 1e12
    asset = _one(doc(app(values=(("2030-01-01", 1e12), ("2030-02-01", -1e12), ("2030-03-01", 10 ** 12)))))
    assert [cents for _, cents in asset.values] == [100_000_000_000_000, -100_000_000_000_000, 100_000_000_000_000]


def test_two_values_on_one_date_take_the_later_entry():
    asset = _one(doc(app(values=(("2030-03-01", 100_000), ("2030-02-01", 90_000), ("2030-03-01", 120_000)))))
    assert asset.values == ((_day("2030-02-01"), 9_000_000), (_day("2030-03-01"), 12_000_000))


_SKIPS = [
    # (case, the document's assets, how the warning names the asset, the reason it gives)
    ("not-an-object", ["family-home"], "#1", "it is not an object"),
    ("id-not-a-slug", [app("Family Home")], "#1", "its id is not"),
    ("id-empty", [app("")], "#1", "its id is not"),
    ("id-not-text", [app(7)], "#1", "its id is not"),
    ("id-leading-dash", [app("-home")], "#1", "its id is not"),
    ("id-too-long", [app("a" * 65)], "#1", "its id is not"),
    ("id-uppercase", [app("Family-home")], "#1", "its id is not"),
    ("id-double-dash", [app("family--home")], "#1", "its id is not"),
    ("id-trailing-dash", [app("family-home-")], "#1", "its id is not"),
    ("id-underscore", [app("family_home")], "#1", "its id is not"),
    ("id-twice", [app(), app(name="Second home")], "#2", "another asset in the file has the same id"),
    *((f"deleted-at-{label}", [app(deleted_at=bad)], "'family-home'",
       "its deleted_at is neither null nor the time it was removed")
      for label, bad in (("false", False), ("empty", ""), ("zero", 0), ("a-list", ["2030-03-02"]))),
    ("no-name", [app(name="  ")], "'family-home'", "it has no name"),
    *((f"name-control-{label}", [app(name=bad)], "'family-home'", "its name holds a control character")
      for label, bad in (("nul", "Family\x00home"), ("newline", "Family home\n"), ("tab", "Family\thome"),
                         ("escape", "\x1b[31mFamily home"), ("delete", "Family home\x7f"))),
    ("owner-unknown", [app(owner="Taylor")], "'family-home'", "its owner is not one of the household's owners"),
    ("class-unknown", [app(asset_class="Boat")], "'family-home'", "its asset class is not one the engine knows"),
    ("liquid-not-a-switch", [app(liquid="yes")], "'family-home'", "its liquid flag is not true or false"),
    ("liquid-debt", [app(asset_class="Other Debt", liquid=True, values=(("2030-01-01", -5_000),))],
     "'family-home'", "it is a debt marked liquid"),
    ("no-values", [app(values=())], "'family-home'", "it has no value"),
    ("values-not-a-list", [{**app(), "values": {"as_of": "2030-01-01", "value": 1}}], "'family-home'",
     "it has no value"),
    ("value-not-an-object", [{**app(), "values": [450_000]}], "'family-home'", "a value is not an as_of and a value"),
    ("value-text", [app(values=(("2030-01-01", "450000"),))], "'family-home'", "a value is not a finite number"),
    ("value-bool", [app(values=(("2030-01-01", True),))], "'family-home'", "a value is not a finite number"),
    ("value-nan", [app(values=(("2030-01-01", float("nan")),))], "'family-home'", "a value is not a finite number"),
    ("value-infinite", [app(values=(("2030-01-01", float("inf")),))], "'family-home'",
     "a value is not a finite number"),
    ("value-past-cents", [app(values=(("2030-01-01", 1.7e308),))], "'family-home'", "a value is out of range"),
    ("value-past-the-database", [app(values=(("2030-01-01", 9.3e16),))], "'family-home'", "a value is out of range"),
    ("value-past-the-range", [app(values=(("2030-01-01", 1e12 + 0.01),))], "'family-home'", "a value is out of range"),
    ("debt-past-the-range", [app(values=(("2030-01-01", -1e13),))], "'family-home'", "a value is out of range"),
    ("date-not-iso", [app(values=(("01/02/2030", 1),))], "'family-home'", "a value's date is not a day"),
    ("date-no-day", [app(values=(("2030-02-30", 1),))], "'family-home'", "a value's date is not a day"),
    ("date-with-time", [app(values=(("2030-02-01T00:00:00", 1),))], "'family-home'", "a value's date is not a day"),
    ("acquired-not-iso", [app(acquired="2030")], "'family-home'", "its acquired date is not a day"),
    ("acquired-after-first-value", [app(acquired="2030-01-02")], "'family-home'",
     "it was acquired after its first value"),
    ("name-of-an-account", [app(name=" SAMPLE checking ")], "'family-home'", "its name is an account's name"),
    ("name-of-a-config-entry", [app(name="sedan")], "'family-home'", "its name is another manual asset's name"),
    ("name-of-an-app-asset", [app("boat", name="Boat"), app(name="BOAT")], "'family-home'",
     "its name is another manual asset's name"),
]


@pytest.mark.parametrize(("assets", "label", "reason"), [c[1:] for c in _SKIPS], ids=[c[0] for c in _SKIPS])
def test_bad_entries_are_skipped_with_a_warning_each(assets, label, reason):
    """Each asset the household could not have meant is left out with one warning naming it (by its id, or by its
    place in the file when it has no usable id) and why, never a value; the accounts.yaml entry with its id, if any,
    counts instead."""
    effective, checks = merged(doc(*assets), (HOME, SEDAN))
    [check] = checks
    assert (check.name, check.status) == ("manual_assets.entry", "warn")
    assert check.detail.startswith(f"manual asset {label} from the app left out: {reason}"), check.detail
    assert not any(ch.isdigit() for ch in check.detail.replace(label, "")), check.detail   # ids and reasons only
    used = {a.id: a.origin for a in effective}
    assert used["family-home"] == ("app" if label == "#2" else "config")
    assert used["sedan"] == "config"
    falls_back = label == "'family-home'"
    assert check.detail.endswith("; the configuration's entry with this id counts instead") is falls_back


def test_a_config_entry_comes_back_when_its_override_is_left_out_and_wins_its_name_back():
    """An app asset overriding one entry and renamed to another entry's name is left out, so the first entry counts
    again; an app asset that took the first entry's name loses it to the entry, which the app can never hide."""
    effective, checks = merged(doc(app(name="Sedan"), app("boat", name="Family home")), (HOME, SEDAN))
    assert [(a.id, a.origin) for a in effective] == [("family-home", "config"), ("sedan", "config")]
    assert [c.detail.split(":")[0] for c in checks] == ["manual asset 'family-home' from the app left out",
                                                        "manual asset 'boat' from the app left out"]


# ------------------------------------------------------------------ the document

def _write(path: Path, content: bytes | dict) -> None:
    path.write_bytes(content if isinstance(content, bytes) else json.dumps(content).encode())


_LOADS = [
    ("not-json", b"{not json", "is not valid JSON"),
    ("not-utf8", b"\xff\xfe\x00", "is not valid JSON"),
    ("nested-too-deep", b"[" * 200_000, "is not valid JSON"),
    ("lone-surrogate", {"version": 1, "assets": [], "note": "\ud800"}, "holds text that is not valid Unicode"),
    ("lone-surrogate-key", {"version": 1, "assets": [{"\udfff": 1}]}, "holds text that is not valid Unicode"),
    ("not-an-object", b"[]", "is not a JSON object with a list of assets"),
    ("assets-not-a-list", {"version": 1, "assets": {"family-home": {}}}, "is not a JSON object with a list of assets"),
    ("no-assets", {"version": 1}, "is not a JSON object with a list of assets"),
    ("version-2", {"version": 2, "assets": []}, "is not version 1"),
    ("version-true", {"version": True, "assets": []}, "is not version 1"),
    ("no-version", {"assets": []}, "is not version 1"),
    ("oversized", {"version": 1, "assets": [], "pad": "x" * ma.MAX_BYTES}, "is larger than 1 MiB"),
    ("unreadable", None, "cannot be read"),
]


@pytest.mark.parametrize(("content", "what"), [c[1:] for c in _LOADS], ids=[c[0] for c in _LOADS])
def test_a_corrupt_or_oversized_document_falls_back_to_the_config_entries_with_a_warning(tmp_path, content, what):
    p = _pipeline(tmp_path, entries=(HOME,), document=None)
    if content is None:
        p.cfg.manual_assets_path.mkdir()                 # a folder where the file should be: it cannot be read
    else:
        _write(p.cfg.manual_assets_path, content)
    loaded, [check] = ma.load(p.cfg.manual_assets_path)
    assert loaded == {"assets": []}
    assert (check.name, check.status) == ("manual_assets.load", "warn") and what in check.detail, check.detail
    assert check.detail == (f"The assets entered in the app could not be read (the stored document {what}) and are left "
                            "out this run; the configuration's entries still count.")
    p.parse_assets()
    assert [(a.id, a.origin) for a in p.manual_assets] == [("family-home", "config")]
    assert [c.name for c in p.checks] == ["manual_assets.load"]


@pytest.mark.parametrize("kind", ["fifo", "dangling-symlink", "folder"])
def test_a_path_that_is_no_regular_file_cannot_be_read_and_nothing_waits_on_it(tmp_path, kind):
    """The loader looks at what the path is before it opens it: a FIFO (an open of one waits until something writes to
    it), a symlink to nothing and a folder each read as "cannot be read", with the load warning."""
    path = tmp_path / "manual_assets.json"
    if kind == "fifo":
        os.mkfifo(path)
    elif kind == "dangling-symlink":
        path.symlink_to(tmp_path / "gone.json")
    else:
        path.mkdir()
    result: list = []
    reader = threading.Thread(target=lambda: result.append(ma.load(path)), daemon=True)
    reader.start()
    reader.join(10)
    assert not reader.is_alive(), "the loader waited on the path"
    [(loaded, checks)] = result
    assert loaded == {"assets": []}
    assert [(c.name, c.status) for c in checks] == [("manual_assets.load", "warn")] and "cannot be read" in checks[0].detail


def test_a_link_to_a_document_is_never_followed(tmp_path):
    """The store writes this file in a folder it shares with the workers, so a name there could hold a link to any
    file a worker can read: it is never followed, whatever it leads to."""
    real = tmp_path / "elsewhere.json"
    _write(real, doc(app()))
    (tmp_path / "manual_assets.json").symlink_to(real)
    loaded, checks = ma.load(tmp_path / "manual_assets.json")
    assert loaded == {"assets": []}
    assert [(c.name, c.status) for c in checks] == [("manual_assets.load", "warn")] and "cannot be read" in checks[0].detail


def test_a_document_of_exactly_the_cap_is_read(tmp_path):
    """1 MiB is the most the loader reads; a document of that size is read like any other."""
    body = json.dumps(doc(app())).encode()
    padded = body[:-1] + b" " * (ma.MAX_BYTES - len(body)) + b"}"
    assert len(padded) == ma.MAX_BYTES
    _write(tmp_path / "manual_assets.json", padded)
    assert ma.load(tmp_path / "manual_assets.json") == (doc(app()), [])


def test_the_etl_never_writes_the_document(tmp_path, monkeypatch):
    """A whole ingest of the minimal household reads the document (its asset is exported) and leaves its bytes and
    its modification time as they were."""
    home = tmp_path / "home"
    shutil.copytree(ROOT / "examples" / "minimal", home)
    monkeypatch.setenv("FF_HOME", str(home))
    path = home / "state" / "family" / "manual_assets.json"
    path.parent.mkdir(parents=True)
    _write(path, doc(app()))
    os.utime(path, ns=(1_900_000_000_000_000_000, 1_900_000_000_000_000_000))
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    assert pipeline.run() == 0
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    listed = json.loads((home / "data" / "exports" / "accounts.json").read_text("utf-8"))["manual_assets"]
    assert [(a["id"], a["origin"]) for a in listed] == [("family-home", "app")]
    # nothing written beside it either: no temporary file, no copy of a damaged one
    assert {p.name for p in path.parent.iterdir()} <= {"learned_rules.json", "manual_assets.json"}


# ------------------------------------------------------------------ the value on a day

def test_a_snapshot_uses_the_value_in_force_on_its_day():
    asset = _one(doc(app(values=(("2030-01-15", 100_000), ("2030-02-20", 110_000), ("2030-04-10", 120_000)))))
    series = _series([asset], _MONTHLY)
    got = {day: (r.value_cents, r.as_of, r.carried_back)
           for day in ("2030-01-31", "2030-02-28", "2030-03-31", "2030-04-30")
           for r in [_row(nw.snapshot_at(series, day))]}
    assert got == {"2030-01-31": (10_000_000, "2030-01-15", False), "2030-02-28": (11_000_000, "2030-02-20", False),
                   "2030-03-31": (11_000_000, "2030-02-20", False), "2030-04-30": (12_000_000, "2030-04-10", False)}
    # a value dated on the day itself is in force on it
    assert _row(nw.snapshot_at(series, "2030-02-20")).value_cents == 11_000_000


def test_a_value_dated_after_the_snapshot_day_is_not_in_force_unless_it_is_the_first():
    asset = _one(doc(app(values=(("2030-02-20", 110_000), ("2030-04-10", 120_000)))))
    series = _series([asset], _MONTHLY)
    early, between = (_row(nw.snapshot_at(series, d)) for d in ("2030-01-31", "2030-03-31"))
    assert (early.value_cents, early.as_of, early.carried_back) == (11_000_000, "2030-02-20", True)
    assert (between.value_cents, between.as_of, between.carried_back) == (11_000_000, "2030-02-20", False)
    assert [nw.manual_value_on(series["manual:family-home"], d) for d in ("2030-01-31", "2030-03-31", "2031-01-01")] == [
        ("2030-02-20", 11_000_000, True), ("2030-02-20", 11_000_000, False), ("2030-04-10", 12_000_000, False)]


def test_before_its_first_value_an_asset_is_carried_back_in_snapshots_and_on_the_daily_line():
    asset = _one(doc(app(values=(("2030-03-15", 300_000),))))
    series = _series([asset], _MONTHLY)
    snap = nw.snapshot_at(series, "2030-01-31")
    row = _row(snap)
    assert (row.value_cents, row.as_of, row.carried_back, row.source, row.manual_id) == (
        30_000_000, "2030-03-15", True, "stated", "family-home")
    assert (snap.missing, snap.coverage) == ((), 1.0)                      # never missing
    assert nw.daily(series)[0] == ("2030-01-31", 1_000_00 + 30_000_000, 1.0)
    # the export says which rows are carried back and which asset each stated row is
    out = exports.export_assets_timeseries(nw.asset_rows(snap), {"2030-01-31": True},
                                           {"2030-01-31": (snap.coverage, nw.missing_names(series, snap))})
    assert [(r["account"], r["manual_id"], r["carried_back"]) for r in out["snapshots"][0]["rows"]] == [
        ("Family home", "family-home", True), ("Sample Checking", None, False)]
    assert out["snapshots"][0]["rows"][0]["institution"] == ""            # an app asset names no institution


def test_an_acquired_asset_has_no_row_before_that_date_and_is_never_missing():
    asset = _one(doc(app(acquired="2030-02-15", values=(("2030-03-01", 50_000),))))
    assert asset.acquired == _day("2030-02-15")
    series = _series([asset], _MONTHLY)
    before, held, valued = (nw.snapshot_at(series, d) for d in ("2030-01-31", "2030-02-28", "2030-03-31"))
    assert _row(before) is None and (before.missing, before.coverage) == ((), 1.0)
    assert (_row(held).as_of, _row(held).carried_back) == ("2030-03-01", True)   # held, before its first value
    assert (_row(valued).as_of, _row(valued).carried_back) == ("2030-03-01", False)
    assert nw.manual_value_on(series["manual:family-home"], "2030-02-14") is None
    assert nw.manual_value_on(series["manual:family-home"], "2030-02-15") == ("2030-03-01", 5_000_000, True)
    line = dict((d, c) for d, c, _ in nw.daily(series))
    assert (line["2030-01-31"], line["2030-02-28"]) == (1_000_00, 1_100_00 + 5_000_000)
    # beside an account not yet observed, coverage counts the series held on the day: the asset not yet acquired is
    # in neither the count nor the whole
    with_savings = _series([asset], _MONTHLY, {"demo_checking_0000": CHECKING, "demo_savings_0000": {
        **CHECKING, "name": "Sample Savings", "kind": "savings"}})
    snap = nw.snapshot_at(with_savings, "2030-01-31")
    assert (snap.missing, snap.coverage) == (("demo_savings_0000",), 0.5)


def test_the_daily_line_has_a_point_wherever_a_manual_asset_changes_within_the_accounts_span():
    """A checking account that reports only on the 12th (a savings account joins on 15 February), a car valued on the
    1st and revalued on the 20th, a boat acquired on the 20th of the next month: the line gains a point on each day a
    manual value starts or an asset is acquired within the account points' span, so every month-end snapshot that
    misses nothing equals the line at its last date on or before it. A manual date outside the span adds no point,
    and coverage counts account series only."""
    obs = [*(_obs("demo_checking_0000", f"2030-{m:02d}-12", 1_000_00 * m) for m in range(1, 6)),
           _obs("demo_savings_0000", "2030-02-15", 300_00), _obs("demo_savings_0000", "2030-04-15", 350_00)]
    accounts = {"demo_checking_0000": CHECKING, "demo_savings_0000": {**CHECKING, "name": "Sample Savings",
                                                                      "kind": "savings"}}
    car = _one(doc(app("sedan", name="Sedan", asset_class="Car", values=(
        ("2029-12-01", 21_000), ("2030-02-01", 20_000), ("2030-02-20", 18_000), ("2030-06-01", 17_000)))))
    boat = _one(doc(app("boat", name="Boat", asset_class="Car", acquired="2030-03-20", values=(("2030-04-05", 6_000),))))
    series = _series([car, boat], obs, accounts)
    line = nw.daily(series)
    assert [(d, c) for d, _, c in line] == [
        ("2030-01-12", 0.5), ("2030-02-01", 0.5), ("2030-02-12", 0.5), ("2030-02-15", 1.0), ("2030-02-20", 1.0),
        ("2030-03-12", 1.0), ("2030-03-20", 1.0), ("2030-04-05", 1.0), ("2030-04-12", 1.0), ("2030-04-15", 1.0),
        ("2030-05-12", 1.0)]
    checked = []
    for day in nw.snapshot_dates(series, "2030-12-31"):
        snap = nw.snapshot_at(series, day)
        if snap.missing:
            continue
        assert sum(r.value_cents for r in snap.rows) == [cents for d, cents, _ in line if d <= day][-1], day
        checked.append(day)
    assert checked == ["2030-02-28", "2030-03-31", "2030-04-30", "2030-05-12"]   # January misses the savings account


def test_a_value_before_any_account_was_observed_starts_no_month():
    early = _one(doc(app(values=(("2029-06-01", 300_000), ("2030-07-01", 310_000)))))
    series = _series([early], _MONTHLY)
    assert nw.snapshot_dates(series, "2030-12-31") == ["2030-01-31", "2030-02-28", "2030-03-31", "2030-04-30",
                                                       "2030-05-31", "2030-06-12"]
    assert nw.current_date(series, "2030-12-31") == "2030-06-12"        # a later value dates nothing either
    # with no account series at all the one date is the newest value of any asset on or before the ingest day, and
    # there is no daily line
    car = _one(doc(app("sedan", name="Sedan", asset_class="Car", values=(("2030-03-01", 20_000),))))
    alone = _series([early, car], [], {})
    assert nw.snapshot_dates(alone, "2030-12-31") == ["2030-07-01"]
    assert nw.daily(alone) == []


def test_with_no_account_point_a_later_value_dated_ahead_waits_for_its_day():
    """No account has a point and one asset has a value dated on the ingest day and one ahead of it: the current
    snapshot is that day's, at that day's value; on the later day it is that day's, at the later value."""
    home = _one(doc(app(values=(("2030-06-01", 300_000), ("2031-01-01", 310_000)))))
    alone = _series([home], [], {})
    # (ingest day, the snapshot's date: the newest value on or before that day, the value in force on it)
    for today, dated, value in (("2030-06-01", "2030-06-01", 300_000_00), ("2030-09-15", "2030-06-01", 300_000_00),
                                ("2031-01-01", "2031-01-01", 310_000_00)):
        assert nw.snapshot_dates(alone, today) == [dated], today
        row = _row(nw.snapshot_at(alone, dated))
        assert (row.value_cents, row.carried_back) == (value, False), today
    # only a value ahead: the ingest day dates the snapshot, the value carried back to it
    ahead = _one(doc(app(values=(("2031-01-01", 310_000),))))
    assert nw.snapshot_dates(_series([ahead], [], {}), "2030-06-01") == ["2030-06-01"]
    assert _row(nw.snapshot_at(_series([ahead], [], {}), "2030-06-01")).carried_back is True


# ------------------------------------------------------------------ the pipeline

def _pipeline(tmp_path: Path, *, entries=(), document: dict | None = None, obs=_MONTHLY,
              accounts: dict | None = None) -> Pipeline:
    """A Pipeline over a household of `accounts` (default: checking and a brokerage) with `entries` as its
    accounts.yaml manual entries and `document` as the store's document, every state path in tmp_path."""
    d = tmp_path / "cfg"
    write_household(d)
    body = {"version": 1, "accounts": accounts or {"demo_checking_0000": CHECKING, "demo_brokerage_0000": BROKERAGE}}
    if entries:
        body["manual_entries"] = [dict(e) for e in entries]
    (d / "accounts.yaml").write_text(yaml.safe_dump(body), "utf-8")
    cfg = isolate(Config(d), tmp_path / "state")
    if document is not None:
        _write(cfg.manual_assets_path, document)
    p = Pipeline(cfg)
    p.balance_obs = list(obs)
    return p


@pytest.mark.parametrize("value", [9.3e16, 1e306, -1e13])
def test_a_value_out_of_range_is_left_out_and_the_ingest_finishes(tmp_path, monkeypatch, value):
    """A value past MAX_MANUAL_VALUE dollars either way (the cents of a larger one overflow the database's integers)
    leaves its asset out with one warning; the whole ingest still writes its exports, and the accounts.yaml entry of
    that id counts instead."""
    home = tmp_path / "home"
    shutil.copytree(ROOT / "examples" / "minimal", home)
    accounts_yaml = home / "config" / "accounts.yaml"
    accounts = yaml.safe_load(accounts_yaml.read_text("utf-8"))
    accounts["manual_entries"] = [dict(HOME)]
    accounts_yaml.write_text(yaml.safe_dump(accounts), "utf-8")
    monkeypatch.setenv("FF_HOME", str(home))
    path = home / "state" / "family" / "manual_assets.json"
    path.parent.mkdir(parents=True)
    _write(path, doc(app(values=(("2030-01-01", value),))))
    assert pipeline.run() == 0
    exports = home / "data" / "exports"
    listed = json.loads((exports / "accounts.json").read_text("utf-8"))["manual_assets"]
    assert [(a["id"], a["origin"]) for a in listed] == [("family-home", "config")]
    checks = [c for c in json.loads((exports / "quality_report.json").read_text("utf-8"))["checks"]
              if c["name"].startswith("manual_assets")]
    assert [(c["name"], c["status"]) for c in checks] == [("manual_assets.entry", "warn")]
    assert "a value is out of range" in checks[0]["detail"]


def test_the_document_sits_beside_the_answers_in_the_state_folder(tmp_path):
    d = tmp_path / "cfg"
    write_household(d)
    (d / "accounts.yaml").write_text(yaml.safe_dump({"accounts": {"demo_checking_0000": CHECKING}}), "utf-8")
    cfg = Config(d)
    assert cfg.manual_assets_path == cfg.answers_path.parent / "manual_assets.json"


def test_entering_an_asset_makes_no_earlier_month_end_partial(tmp_path):
    """A home entered today, with nothing earlier, is carried back into every month end: no month end misses
    anything it did not miss before, and each holds the home at today's value."""
    obs = [*_MONTHLY, _obs("demo_brokerage_0000", "2030-03-20", 5_000_00)]   # the brokerage joins in March
    before = _pipeline(tmp_path / "before", obs=obs)
    before.parse_assets()
    after = _pipeline(tmp_path / "after", obs=obs, document=doc(app(values=(("2030-06-12", 450_000),))))
    after.parse_assets()
    assert list(after.snapshot_coverage) == list(before.snapshot_coverage)
    assert [m for _, m in after.snapshot_coverage.values()] == [m for _, m in before.snapshot_coverage.values()]
    assert [m for _, m in before.snapshot_coverage.values()][:2] == [["Sample Brokerage"]] * 2   # the control
    homes = {r.snapshot_date: (r.value_cents, r.as_of, r.carried_back) for r in after.asset_rows
             if r.manual_id == "family-home"}
    assert homes == {day: (45_000_000, "2030-06-12", day != "2030-06-12") for day in before.snapshot_coverage}


def test_the_effective_list_is_exported_with_its_origin(tmp_path):
    p = _pipeline(tmp_path, entries=(SEDAN, {**HOME, "acquired": "2029-12-01"}),
                  document=doc(app(values=(("2030-02-01", 450_000), ("2030-01-01", 440_000.5)))))
    p.parse_assets()
    out = tmp_path / "out"
    p._export_accounts(out)
    accounts = json.loads((out / "accounts.json").read_text("utf-8"))
    assert list(accounts) == ["accounts", "manual_assets", "manual_overridden"]
    # the entry the app asset of its id hides, in the same shape, its origin the config's
    assert accounts["manual_overridden"] == [
        {"id": "family-home", "name": "Family home", "institution": "Sample Assessor", "owner": "Joint",
         "asset_class": "House", "liquid": False, "acquired": "2029-12-01",
         "values": [{"as_of": "2030-01-01", "value": 400_000.0}], "origin": "config"}]
    assert accounts["manual_assets"] == [
        {"id": "family-home", "name": "Family home", "institution": None, "owner": "Joint", "asset_class": "House",
         "liquid": False, "acquired": None, "values": [{"as_of": "2030-01-01", "value": 440_000.5},
                                                      {"as_of": "2030-02-01", "value": 450_000.0}], "origin": "app"},
        {"id": "sedan", "name": "Sedan", "institution": "Sample Dealer", "owner": "Sam", "asset_class": "Car",
         "liquid": False, "acquired": None, "values": [{"as_of": "2030-01-01", "value": 20_000.0}],
         "origin": "config"}]
    assert p._setup()["manual_assets"] == 2                 # the effective count: the override replaced the entry


def test_month_end_snapshots_equal_the_daily_line_with_manual_assets(tmp_path):
    """With an asset whose first value is mid-history, one acquired mid-history and a config entry, every month end
    that misses nothing totals the daily line's value at its last date on or before it."""
    obs = [*_MONTHLY, _obs("demo_brokerage_0000", "2030-01-31", 5_000_00), _obs("demo_brokerage_0000", "2030-04-30",
                                                                                 5_500_00)]
    p = _pipeline(tmp_path, entries=(SEDAN,), obs=obs, document=doc(
        app(values=(("2030-03-15", 450_000), ("2030-05-10", 460_000))),
        app("boat", name="Boat", asset_class="Other Debt", acquired="2030-02-10",
            values=(("2030-02-10", -5_000), ("2030-04-02", -4_000)))))
    p.parse_assets()
    line = nw.daily(p.networth_series)
    checked = []
    for day, (_, missing) in p.snapshot_coverage.items():
        assert missing == [], day
        value = [cents for d, cents, _ in line if d <= day][-1]
        assert sum(r.value_cents for r in p.asset_rows if r.snapshot_date == day) == value, day
        checked.append(day)
    assert checked == ["2030-01-31", "2030-02-28", "2030-03-31", "2030-04-30", "2030-05-31", "2030-06-12"]
    # the control: the carried-back home and the boat before its acquisition are both exercised
    carried = {r.snapshot_date for r in p.asset_rows if r.carried_back}
    assert carried == {"2030-01-31", "2030-02-28"}
    assert {r.snapshot_date for r in p.asset_rows if r.manual_id == "boat"} == set(checked[1:])
