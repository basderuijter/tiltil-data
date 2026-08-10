"""Opslag van controles en de audittrail (SQLite).

SRS blijft de bron voor orders en voorraad; hier staat alleen wat er tijdens
de controle gebeurd is: wie telde wat, welke afwijkingen, wie gaf akkoord.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .models import Controle, ControleRegel

SCHEMA = """
CREATE TABLE IF NOT EXISTS controles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ph_code TEXT NOT NULL,
    order_referentie TEXT NOT NULL,
    medewerker TEXT NOT NULL DEFAULT '',
    gestart_op TEXT NOT NULL,
    akkoord_op TEXT,
    akkoord_door TEXT NOT NULL DEFAULT '',
    afwijking_notitie TEXT NOT NULL DEFAULT '',
    regels TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_controles_ph ON controles (ph_code);

CREATE TABLE IF NOT EXISTS gebeurtenissen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    controle_id INTEGER,
    ph_code TEXT NOT NULL,
    moment TEXT NOT NULL,
    soort TEXT NOT NULL,
    medewerker TEXT NOT NULL DEFAULT '',
    details TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_gebeurtenissen_ph ON gebeurtenissen (ph_code);
"""


def _nu() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _regels_naar_json(regels: list[ControleRegel]) -> str:
    return json.dumps(
        [
            {
                "barcode": r.barcode,
                "omschrijving": r.omschrijving,
                "aantal_verwacht": r.aantal_verwacht,
                "aantal_geteld": r.aantal_geteld,
                "conditie": r.conditie,
                "notitie": r.notitie,
                "sku": r.sku,
            }
            for r in regels
        ],
        ensure_ascii=False,
    )


def _regels_uit_json(ruw: str) -> list[ControleRegel]:
    return [ControleRegel(**regel) for regel in json.loads(ruw or "[]")]


class Opslag:
    def __init__(self, pad: Path | str):
        self.pad = Path(pad)
        self.pad.parent.mkdir(parents=True, exist_ok=True)
        self._verbinding = sqlite3.connect(self.pad, check_same_thread=False)
        self._verbinding.row_factory = sqlite3.Row
        self._verbinding.executescript(SCHEMA)
        self._verbinding.commit()

    # -- controles -------------------------------------------------------

    def open_controle(self, ph_code: str) -> Controle | None:
        """De lopende (nog niet akkoord gegeven) controle van een PH."""
        rij = self._verbinding.execute(
            "SELECT * FROM controles WHERE ph_code = ? AND akkoord_op IS NULL "
            "ORDER BY id DESC LIMIT 1",
            (ph_code,),
        ).fetchone()
        return self._naar_controle(rij) if rij else None

    def laatste_controle(self, ph_code: str) -> Controle | None:
        rij = self._verbinding.execute(
            "SELECT * FROM controles WHERE ph_code = ? ORDER BY id DESC LIMIT 1",
            (ph_code,),
        ).fetchone()
        return self._naar_controle(rij) if rij else None

    def haal_controle(self, controle_id: int) -> Controle | None:
        rij = self._verbinding.execute(
            "SELECT * FROM controles WHERE id = ?", (controle_id,)
        ).fetchone()
        return self._naar_controle(rij) if rij else None

    def bewaar_nieuw(self, controle: Controle) -> Controle:
        moment = controle.gestart_op or datetime.now()
        cursor = self._verbinding.execute(
            "INSERT INTO controles (ph_code, order_referentie, medewerker, gestart_op, regels)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                controle.ph_code,
                controle.order_referentie,
                controle.medewerker,
                moment.isoformat(timespec="seconds"),
                _regels_naar_json(controle.regels),
            ),
        )
        self._verbinding.commit()
        controle.id = int(cursor.lastrowid)
        controle.gestart_op = moment
        return controle

    def bewaar(self, controle: Controle) -> None:
        if controle.id is None:
            raise ValueError("Controle zonder id kan niet bijgewerkt worden.")
        self._verbinding.execute(
            "UPDATE controles SET medewerker = ?, akkoord_op = ?, akkoord_door = ?,"
            " afwijking_notitie = ?, regels = ? WHERE id = ?",
            (
                controle.medewerker,
                controle.akkoord_op.isoformat(timespec="seconds")
                if controle.akkoord_op
                else None,
                controle.akkoord_door,
                controle.afwijking_notitie,
                _regels_naar_json(controle.regels),
                controle.id,
            ),
        )
        self._verbinding.commit()

    def _naar_controle(self, rij: sqlite3.Row) -> Controle:
        return Controle(
            id=int(rij["id"]),
            ph_code=rij["ph_code"],
            order_referentie=rij["order_referentie"],
            medewerker=rij["medewerker"],
            gestart_op=datetime.fromisoformat(rij["gestart_op"]),
            akkoord_op=datetime.fromisoformat(rij["akkoord_op"]) if rij["akkoord_op"] else None,
            akkoord_door=rij["akkoord_door"],
            afwijking_notitie=rij["afwijking_notitie"],
            regels=_regels_uit_json(rij["regels"]),
        )

    # -- audittrail ------------------------------------------------------

    def log(
        self,
        ph_code: str,
        soort: str,
        *,
        controle_id: int | None = None,
        medewerker: str = "",
        details: dict | None = None,
    ) -> None:
        self._verbinding.execute(
            "INSERT INTO gebeurtenissen (controle_id, ph_code, moment, soort, medewerker,"
            " details) VALUES (?, ?, ?, ?, ?, ?)",
            (
                controle_id,
                ph_code,
                _nu(),
                soort,
                medewerker,
                json.dumps(details or {}, ensure_ascii=False),
            ),
        )
        self._verbinding.commit()

    def gebeurtenissen(self, ph_code: str, limiet: int = 50) -> list[dict]:
        rijen = self._verbinding.execute(
            "SELECT * FROM gebeurtenissen WHERE ph_code = ? ORDER BY id DESC LIMIT ?",
            (ph_code, limiet),
        ).fetchall()
        return [
            {
                "moment": r["moment"],
                "soort": r["soort"],
                "medewerker": r["medewerker"],
                "details": json.loads(r["details"]),
            }
            for r in rijen
        ]

    def afgeronde_controles(self, limiet: int = 25) -> list[Controle]:
        rijen = self._verbinding.execute(
            "SELECT * FROM controles WHERE akkoord_op IS NOT NULL ORDER BY akkoord_op DESC"
            " LIMIT ?",
            (limiet,),
        ).fetchall()
        return [self._naar_controle(r) for r in rijen]

    def close(self) -> None:
        self._verbinding.close()
