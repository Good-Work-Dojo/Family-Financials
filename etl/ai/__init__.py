# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""The optional AI step: the names of vendors no household rule places, and nothing else, may be sent to a model to
suggest a spending category.

The modules here split by what they may reach. `policy` (what may be sent), `candidates` (which rows' vendors are
asked about, and which of those are never sent), `request` (the exact request body) and `cli` (`python -m etl ai
preview`) are offline: they import nothing that opens a socket, and the offline pipeline may import them. Anything
that talks to a provider lives in its own module, which neither the pipeline nor those modules import
(tests/test_offline_imports.py).
"""
