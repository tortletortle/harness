"""Persistent, atomic total-request cap for the isolated real-model experiment.

Unlike a per-server journal, this ledger survives new temporary servers. A
missing, corrupt, locked, or exhausted ledger rejects calls before any network
request. The caller deliberately precharges known earlier usage plus a reserve
for interrupted requests when creating the ledger once.
"""

from contextlib import closing
from pathlib import Path
import sqlite3

TOTAL_CAP = 100


class BudgetUnavailable(RuntimeError):
    """No further external model call is permitted."""


def create_budget(path: Path, *, precharged: int) -> None:
    """One-time setup; never reset or silently recreate an existing ledger."""
    if not isinstance(precharged, int) or isinstance(precharged, bool) or not 0 <= precharged <= TOTAL_CAP:
        raise ValueError("invalid precharged request count")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb"):
        pass
    with closing(sqlite3.connect(str(path))) as conn:
        conn.execute("CREATE TABLE request_budget (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                     "cap INTEGER NOT NULL CHECK(cap=100), "
                     "used INTEGER NOT NULL CHECK(used>=0), "
                     "precharged INTEGER NOT NULL CHECK(precharged>=0))")
        conn.execute("INSERT INTO request_budget VALUES (1, ?, ?, ?)",
                     (TOTAL_CAP, precharged, precharged))
        conn.commit()


def _row(conn):
    row = conn.execute("SELECT cap, used, precharged FROM request_budget WHERE singleton=1").fetchone()
    if (row is None or any(not isinstance(value, int) for value in row)
            or row[0] != TOTAL_CAP
            or not 0 <= row[2] <= row[1] <= TOTAL_CAP):
        raise BudgetUnavailable("invalid cumulative budget ledger")
    return row


def read_budget(path: Path) -> dict:
    if not Path(path).is_file():
        raise BudgetUnavailable("missing cumulative budget ledger")
    try:
        with closing(sqlite3.connect(str(path), timeout=20)) as conn:
            cap, used, precharged = _row(conn)
            return {"cap": cap, "used": used, "precharged": precharged}
    except sqlite3.Error as exc:
        raise BudgetUnavailable("unavailable cumulative budget ledger") from exc


def claim_budget(path: Path) -> int:
    """Durably charge exactly one request before it is sent; return total used."""
    if not Path(path).is_file():
        raise BudgetUnavailable("missing cumulative budget ledger")
    try:
        with closing(sqlite3.connect(str(path), timeout=20)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cap, used, _ = _row(conn)
            if used >= cap:
                raise BudgetUnavailable("cumulative model request cap reached")
            conn.execute("UPDATE request_budget SET used=? WHERE singleton=1", (used + 1,))
            conn.commit()
            return used + 1
    except sqlite3.Error as exc:
        raise BudgetUnavailable("unavailable cumulative budget ledger") from exc
