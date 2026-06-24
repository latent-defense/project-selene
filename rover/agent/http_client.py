"""Robust HTTP GET with bounded retries and timeouts.

Every fetch returns a (data, meta) pair so the mapper can record exactly what
happened per endpoint — status code, latency, and any error — making the map
self-describing and gaps detectable downstream.
"""
import time
import httpx


def get_json(url: str, timeout: float = 5.0, retries: int = 2):
    """GET a URL expecting JSON.

    Returns (data, meta). On a clean 200, data is the parsed body. On an expected
    404 (e.g. /comms), data is None and meta['http_status'] == 404. On transient
    errors we retry with backoff; the final failure is captured in meta['error'].
    """
    last_error = None
    started = time.monotonic()
    for attempt in range(retries + 1):
        try:
            resp = httpx.get(url, timeout=timeout)
            latency_ms = round((time.monotonic() - started) * 1000, 1)
            meta = {"http_status": resp.status_code, "latency_ms": latency_ms, "error": None}
            if resp.status_code == 200:
                try:
                    return resp.json(), meta
                except Exception as e:  # 200 but unparseable
                    meta["error"] = f"json_decode: {e}"
                    return None, meta
            # Non-200 (e.g. 404 on /comms) is a definitive answer, not a transient failure.
            return None, meta
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last_error = f"{type(e).__name__}: {e}"
            if attempt < retries:
                time.sleep(0.3 * (attempt + 1))
    latency_ms = round((time.monotonic() - started) * 1000, 1)
    return None, {"http_status": None, "latency_ms": latency_ms, "error": last_error}
