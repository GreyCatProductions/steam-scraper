import sqlite3
from contextlib import closing
from pathlib import Path

from playercount_fetching.schema.appPlayerCount import AppPlayerCount

DB_TIMEOUT_SECONDS = 30

# WITHOUT ROWID stores the rows in the primary key b-tree itself, instead of a rowid table plus a separate index
CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS playercounts (
        appid       INTEGER NOT NULL,
        timestamp   INTEGER NOT NULL,
        playercount INTEGER NOT NULL,
        PRIMARY KEY (appid, timestamp)
    ) WITHOUT ROWID
"""

UPSERT = """
    INSERT INTO playercounts (appid, timestamp, playercount) VALUES (?, ?, ?)
    ON CONFLICT (appid, timestamp) DO UPDATE SET playercount = excluded.playercount
"""


class PlayercountFile:
    """Saves player counts into a standalone sqlite file with a single table: playercounts(appid, timestamp, playercount).
    Apps steamcharts doesn't track get no rows.
    Batches are upserted straight into the table, so it accumulates the history of every round; on a clash the newer
    fetch wins. To avoid a second table, round progress is kept in the file header (PRAGMA user_version): the last
    saved appid, written in the same transaction as its batch, so an interrupted round resumes after it.
    Apps are fetched in ascending appid order, which is what makes a single "last appid" enough."""

    def __init__(self, output: Path):
        self.output = output

    def _connect(self) -> sqlite3.Connection:
        # autocommit mode, so transactions are only the explicit BEGIN/COMMIT below
        return sqlite3.connect(self.output, timeout=DB_TIMEOUT_SECONDS, isolation_level=None)

    def start_round(self, restart: bool) -> int:
        """Creates the table if needed. Returns the last appid saved by an interrupted round, or 0 for a fresh round."""
        with closing(self._connect()) as conn:
            conn.execute(CREATE_TABLE)
            if restart:
                conn.execute("PRAGMA user_version = 0")
            return conn.execute("PRAGMA user_version").fetchone()[0]

    def save_batch(self, batch: list[AppPlayerCount]) -> int:
        """Upserts the entries of a batch and moves the round progress past it in one transaction. Returns rows written."""
        if not batch:
            return 0
        rows = [(app.appid, e.timestamp, e.playercount) for app in batch for e in app.entries]
        last_appid = max(app.appid for app in batch)

        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.executemany(UPSERT, rows)
                # PRAGMA values can't be bound parameters; int() keeps it a plain number
                conn.execute(f"PRAGMA user_version = {int(last_appid)}")
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return len(rows)

    def finish_round(self) -> None:
        """Resets the round progress, so the next run starts a fresh round."""
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA user_version = 0")


def load_playercounts(path: Path) -> dict[int, list[tuple[int, int]]]:
    """Reads a file written by PlayercountFile into {appid: [(timestamp, playercount), ...]}."""
    result: dict[int, list[tuple[int, int]]] = {}
    with closing(sqlite3.connect(path, timeout=DB_TIMEOUT_SECONDS)) as conn:
        for appid, ts, count in conn.execute("SELECT appid, timestamp, playercount FROM playercounts ORDER BY appid, timestamp"):
            result.setdefault(appid, []).append((ts, count))
    return result
