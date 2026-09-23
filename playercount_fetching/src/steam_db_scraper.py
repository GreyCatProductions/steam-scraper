import logging
import time

import requests

from playercount_fetching.schema.appPlayerCount import PlayercountEntry

log = logging.getLogger(__name__)

URL_STRUCTURE = "https://steamcharts.com/app/{APP_ID}/chart-data.json"
REQUEST_COOLDOWN_SECONDS = 0.5
BLOCKED_STATUSES = {429, 503}  # rate limited / temporarily unavailable
MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 10


def get_player_numbers(app_id: int) -> list[PlayercountEntry]:
    """Fetches the player count history for an app from steamcharts. 
    Returns [] if steamcharts doesn't track it."""
    
    url = URL_STRUCTURE.format(APP_ID=app_id)
    for attempt in range(MAX_RETRIES + 1):
        r = requests.get(url, timeout=30)
        if r.status_code not in BLOCKED_STATUSES or attempt == MAX_RETRIES:
            break
        # Blocked for spamming: honor Retry-After if given, otherwise back off exponentially
        retry_after = r.headers.get("Retry-After", "")
        wait = int(retry_after) if retry_after.isdigit() else BACKOFF_BASE_SECONDS * 2 ** attempt
        log.warning("appid %s: HTTP %s, retrying in %ss (attempt %d/%d)",
                    app_id, r.status_code, wait, attempt + 1, MAX_RETRIES)
        time.sleep(wait)

    if r.status_code == 404:
        return []
    r.raise_for_status()

    return [
        PlayercountEntry(timestamp=ts_ms // 1000, playercount=count)
        for ts_ms, count in r.json()
        if count is not None
    ]

if __name__ == "__main__":
    import argparse
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser(description="Fetch and print the steamcharts player history for one app")
    parser.add_argument("appid", type=int, help="Steam appid, e.g. 730")
    args = parser.parse_args()

    entries = get_player_numbers(args.appid)
    for e in entries:
        ts = datetime.fromtimestamp(e.timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        print(f"{ts}  {e.playercount:>10,}")


    print(f"{len(entries)} entries for appid {args.appid}")
