# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The statement-file importers: one module per native format and the OFX / QFX reader (ofx.py), each registering its
parse under its format name in `registry` when it loads. Importing this package loads them, so `registry.formats()`
holds every native format and `ofx`."""
from . import bofa_bank, chase_card, chase_checking, ofx, registry

__all__ = ["bofa_bank", "chase_card", "chase_checking", "ofx", "registry"]
