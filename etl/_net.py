# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms: see NOTICE
"""Shared plumbing for the ETL's ONLINE modules (the feed fetchers).

Everything else in the ETL is offline by design — do not import this from the
parse/enrich/export path. Every fetcher needs the same three things: UTC now, a
verified TLS context, and a browser User-Agent (SimpleFIN's Bridge sits behind
Cloudflare, which bans the default "Python-urllib" UA with error 1010).
"""
from __future__ import annotations

import datetime as dt
import ssl

# Per-attempt cap. The Bridge normally answers in ~3s, but it aggregates from the
# banks live, so a cold connection can legitimately run long — keep this generous
# and let the caller's retries (simplefin._RETRY_WAITS) handle true stalls, which
# never recover no matter how long we wait.
TIMEOUT = 90
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def ssl_context() -> ssl.SSLContext:
    """Verified TLS context. macOS's framework Python ships no CA bundle, so use
    certifi's when available; otherwise fall back to the system default."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()
