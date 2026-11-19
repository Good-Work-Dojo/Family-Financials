# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Generator of an invented demo household: a double-entry ledger that writes every input the ETL reads, so running
balances, statement balances and feed balances agree by construction. Pure standard library; nothing here imports
`etl`."""
