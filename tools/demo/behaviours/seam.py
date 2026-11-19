# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The seam between statement files and the feed, on joint checking: two identical purchases (same day, amount and
descriptor) in the month its two half-year exports share, so both exports list both rows and each is stored once. The
merchant has no other charge on checking, so the pair adds no series for the recurring detector. A plan whose
checking has no cutover, no two exports that overlap, or an overlap too short for the twins' day has no twins."""
from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from .. import household as hh
from .spending import PANERA

if TYPE_CHECKING:
    from ..plan import Plan

TWIN_CENTS = 1_437
TWIN_DAY = 10                    # the day of the shared month the twins are bought on


def twins(plan: Plan) -> None:
    if not plan.has(hh.CHECKING) or plan.cutover(hh.CHECKING) is None:
        return
    exports = sorted(plan.exports(hh.CHECKING), key=lambda e: e.first)
    shared = [(b.first, a.last) for a, b in zip(exports, exports[1:], strict=False) if b.first <= a.last]
    if not shared:
        return
    day = shared[0][0] + timedelta(days=TWIN_DAY - 1)
    if not day <= shared[0][1]:
        return
    pair = tuple(plan.post(PANERA.purchase(hh.CHECKING, day, -TWIN_CENTS, card=False, bank_type="DEBIT_CARD"))
                 for _ in range(2))
    plan.plant("seam.account", hh.CHECKING)
    plan.plant("seam.cutover", plan.cutover(hh.CHECKING))
    plan.plant("seam.twins", pair)
