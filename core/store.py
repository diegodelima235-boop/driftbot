"""SQLite: lembra o que ja foi notificado e detecta queda de preco."""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

from .models import Listing

log = logging.getLogger("driftbot.store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    uid          TEXT PRIMARY KEY,
    fingerprint  TEXT,
    source       TEXT,
    title        TEXT,
    url          TEXT,
    price        INTEGER,
    best_price   INTEGER,
    city         TEXT,
    first_seen   REAL,
    last_seen    REAL,
    notify_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_fp ON seen(fingerprint);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------------
    def triage(self, li: Listing, drop_pct: int) -> tuple[bool, str]:
        """(deve_notificar, motivo). Registra/atualiza a linha de qualquer jeito."""
        now = time.time()
        row = self.db.execute("SELECT * FROM seen WHERE uid=?", (li.uid,)).fetchone()

        if row is None:
            # mesmo carro reanunciado com outro id? (loja republicando)
            dup = self.db.execute(
                "SELECT uid FROM seen WHERE fingerprint=?", (li.fingerprint,)
            ).fetchone()
            self.db.execute(
                "INSERT INTO seen (uid,fingerprint,source,title,url,price,best_price,"
                "city,first_seen,last_seen,notify_count) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (li.uid, li.fingerprint, li.source, li.title, li.url, li.price,
                 li.price, li.city, now, now, 0),
            )
            self.db.commit()
            if dup:
                return False, "republicado"
            return True, "novo"

        # ja conhecido - atualiza e ve se o preco caiu
        best = row["best_price"]
        notify, reason = False, "ja visto"

        # Ainda na fila: passou no filtro antes mas foi cortado pelo limite de alertas
        # do ciclo. Sem isso, tudo que excede max_alerts_per_scan some pra sempre.
        if row["notify_count"] == 0:
            notify, reason = True, "novo"

        if li.price and best and li.price < best:
            queda = (best - li.price) / best * 100
            if queda >= drop_pct:
                notify, reason = True, f"baixou {queda:.0f}% (de R$ {best:,.0f})".replace(",", ".")

        new_best = min(x for x in (best, li.price) if x) if (best or li.price) else None
        self.db.execute(
            "UPDATE seen SET last_seen=?, price=?, best_price=?, title=?, url=? WHERE uid=?",
            (now, li.price, new_best, li.title, li.url, li.uid),
        )
        self.db.commit()
        return notify, reason

    def mark_notified(self, li: Listing) -> None:
        self.db.execute(
            "UPDATE seen SET notify_count = notify_count + 1 WHERE uid=?", (li.uid,)
        )
        self.db.commit()

    def stats(self) -> dict:
        cur = self.db.execute(
            "SELECT COUNT(*) n, SUM(notify_count>0) notificados FROM seen"
        ).fetchone()
        return {"conhecidos": cur["n"] or 0, "notificados": cur["notificados"] or 0}
