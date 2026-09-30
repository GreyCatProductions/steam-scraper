import logging

import requests

from playercount_fetching.schema.appPlayerCount import PlayercountEntry
from playercount_fetching.src.proxy_pool import ProxyPool, as_requests_proxies, label

log = logging.getLogger(__name__)

URL_STRUCTURE = "https://steamcharts.com/app/{APP_ID}/chart-data.json"
REQUEST_COOLDOWN_SECONDS_MIN = 1
REQUEST_COOLDOWN_SECONDS_MAX = 3
BLOCKED_STATUSES = {429, 503}
MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 10
DEAD_PROXY_BENCH_SECONDS = 300


def get_player_numbers(app_id: int, pool: ProxyPool) -> list[PlayercountEntry]:
    """Fetches the player count history for an app from steamcharts.
    Returns [] if steamcharts doesn't track it.
    Every request goes out through the next available proxy in the pool. A blocked or unreachable
    proxy is benched, and the retry goes out through another one (or waits, if it's the only one)."""

    url = URL_STRUCTURE.format(APP_ID=app_id)
    for attempt in range(MAX_RETRIES + 1):
        proxy = pool.get()
        try:
            r = requests.get(url, timeout=30, proxies=as_requests_proxies(proxy))
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt == MAX_RETRIES:
                raise
            wait = DEAD_PROXY_BENCH_SECONDS if proxy else BACKOFF_BASE_SECONDS * 2 ** attempt
            # requests wraps urllib3's MaxRetryError, whose .reason holds the actual cause (e.g. a 407 from the proxy)
            reason = getattr(e.args[0], "reason", e) if e.args else e
            log.warning("appid %s: %s unreachable (%s: %s), benching it for %ss (attempt %d/%d)",
                        app_id, label(proxy), type(e).__name__, reason, wait, attempt + 1, MAX_RETRIES)
            pool.bench(proxy, wait)
            continue

        if r.status_code not in BLOCKED_STATUSES or attempt == MAX_RETRIES:
            break

        retry_after = r.headers.get("Retry-After", "")
        wait = int(retry_after) if retry_after.isdigit() else BACKOFF_BASE_SECONDS * 2 ** attempt
        log.warning("appid %s: HTTP %s via %s, benching it for %ss (attempt %d/%d)",
                    app_id, r.status_code, label(proxy), wait, attempt + 1, MAX_RETRIES)
        pool.bench(proxy, wait)

    if r.status_code == 404: # type: ignore
        return []
    r.raise_for_status() # type: ignore

    return [
        PlayercountEntry(timestamp=ts_ms // 1000, playercount=count)
        for ts_ms, count in r.json() # type: ignore
        if count is not None
    ]

if __name__ == "__main__":
    import argparse
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser(description="Fetch and print the steamcharts player history for one app")
    parser.add_argument("appid", type=int, help="Steam appid, e.g. 730")
    args = parser.parse_args()

    entries = get_player_numbers(args.appid, ProxyPool.direct((0, 0)))
    for e in entries:
        ts = datetime.fromtimestamp(e.timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        print(f"{ts}  {e.playercount:>10,}")


    print(f"{len(entries)} entries for appid {args.appid}")
