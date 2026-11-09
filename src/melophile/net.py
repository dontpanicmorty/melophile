"""The only network access Melophile has: AcoustID and MusicBrainz, both optional and both used only when the
person asks. Requests use the system's trusted certificates, a time limit, and each service's rate limit."""
from __future__ import annotations

import json
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import __version__

USER_AGENT = f"Melophile/{__version__} ( https://github.com/dontpanicmorty/melophile )"
TIMEOUT = 20


class NetError(RuntimeError):
    pass


def _context() -> ssl.SSLContext:
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # the macOS keychain's certificates
    except Exception:
        return ssl.create_default_context()


_ctx = None


class RateLimit:
    """At most one request every `interval` seconds, across threads."""

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                time.sleep(self._next - now)
            self._next = time.monotonic() + self.interval


MUSICBRAINZ_RATE = RateLimit(1.1)  # MusicBrainz asks for no more than one request a second
ACOUSTID_RATE = RateLimit(0.34)    # AcoustID allows three a second


def get_json(url: str, params: dict | None = None, rate: RateLimit | None = None, data: dict | None = None,
             retries: int = 2) -> dict:
    global _ctx
    if _ctx is None:
        _ctx = _context()
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    body = urllib.parse.urlencode(data).encode() if data else None
    for attempt in range(retries + 1):
        if rate:
            rate.wait()
        req = urllib.request.Request(full, data=body, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 503) and attempt < retries:  # asked to slow down
                time.sleep(2 * (attempt + 1))
                continue
            if exc.code == 404:
                return {}
            raise NetError(f"{urllib.parse.urlparse(url).netloc} answered {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            raise NetError(f"Couldn't reach {urllib.parse.urlparse(url).netloc}: {exc}") from None
    raise NetError("unreachable")
