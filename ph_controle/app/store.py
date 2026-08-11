"""Opslag van controles en de audittrail (SQLite).

SRS blijft de bron voor orders en voorraad; hier staat alleen wat er tijdens
de controle gebeurd is: wie telde wat, welke afwijkingen, wie gaf akkoord.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .models import Controle, ControleRegel, Observatie, Onderzoek
from .verzending.models import Label

# Kolommen die later bij de controles zijn gekomen (kratten en verzending).
# Bestaande databases worden bij het opstarten bijgewerkt.
EXTRA_KOLOMMEN: dict[str, str] = {
    "kratten": "INTEGER NOT NULL DEFAULT 1",
    "actieve_krat": "INTEGER NOT NULL DEFAULT 1",
    "is_deellevering": "INTEGER NOT NULL DEFAULT 0",
    "levering_referentie": "TEXT NOT NULL DEFAULT ''",
    "zending_methode": "TEXT NOT NULL DEFAULT ''",
    "zending_vervoerder": "TEXT NOT NULL DEFAULT ''",
    "tracking": "TEXT NOT NULL DEFAULT '[]'",
    "labels_geprint": "INTEGER NOT NULL DEFAULT 0",
    "label_fout": "TEXT NOT NULL DEFAULT ''",
    "kratverdeling_exact": "INTEGER NOT NULL DEFAULT 1",
    "giftcards_overgeslagen": "INTEGER NOT NULL DEFAULT 0",
}

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

CREATE TABLE IF NOT EXISTS labels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    controle_id INTEGER,
    ph_code TEXT NOT NULL,
    krat INTEGER NOT NULL DEFAULT 1,
    parcel_id TEXT NOT NULL DEFAULT '',
    mime_type TEXT NOT NULL DEFAULT 'application/pdf',
    tracking_number TEXT NOT NULL DEFAULT '',
    bestand_base64 TEXT NOT NULL DEFAULT '',
    gemaakt_op TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_labels_controle ON labels (controle_id);

CREATE TABLE IF NOT EXISTS ph_observaties (
    ph_code TEXT PRIMARY KEY,
    order_referentie TEXT NOT NULL DEFAULT '',
    eerst_gezien TEXT NOT NULL,
    laatst_gewijzigd TEXT NOT NULL,
    vingerafdruk TEXT NOT NULL DEFAULT '',
    compleet_sinds TEXT
);

CREATE TABLE IF NOT EXISTS onderzoeken (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ph_code TEXT NOT NULL,
    reden TEXT NOT NULL DEFAULT '',
    notitie TEXT NOT NULL DEFAULT '',
    geopend_op TEXT NOT NULL,
    geopend_door TEXT NOT NULL DEFAULT '',
    opgelost_op TEXT,
    opgelost_door TEXT NOT NULL DEFAULT '',
    oplossing TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_onderzoeken_ph ON onderzoeken (ph_code);

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
                "per_krat": r.per_krat,
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
        self._migreer()
        self._verbinding.commit()

    def _migreer(self) -> None:
        """Vul kolommen aan die er in een oudere database nog niet waren."""
        bestaand = {
            rij["name"] for rij in self._verbinding.execute("PRAGMA table_info(controles)")
        }
        for naam, definitie in EXTRA_KOLOMMEN.items():
            if naam not in bestaand:
                self._verbinding.execute(
                    f"ALTER TABLE controles ADD COLUMN {naam} {definitie}"
                )

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

    def laatste_controles(self) -> dict[str, Controle]:
        """De meest recente controle per PH — in één query voor het overzicht."""
        rijen = self._verbinding.execute(
            "SELECT * FROM controles WHERE id IN"
            " (SELECT MAX(id) FROM controles GROUP BY ph_code)"
        ).fetchall()
        return {rij["ph_code"]: self._naar_controle(rij) for rij in rijen}

    def controletijden(self, sinds: datetime) -> list[float]:
        """Doorlooptijd in seconden van de controles die sinds dat moment akkoord kregen."""
        rijen = self._verbinding.execute(
            "SELECT gestart_op, akkoord_op FROM controles WHERE akkoord_op IS NOT NULL"
            " AND akkoord_op >= ?",
            (sinds.isoformat(timespec="seconds"),),
        ).fetchall()
        return [
            (datetime.fromisoformat(r["akkoord_op"]) - datetime.fromisoformat(r["gestart_op"]))
            .total_seconds()
            for r in rijen
        ]

    def haal_controle(self, controle_id: int) -> Controle | None:
        rij = self._verbinding.execute(
            "SELECT * FROM controles WHERE id = ?", (controle_id,)
        ).fetchone()
        return self._naar_controle(rij) if rij else None

    def bewaar_nieuw(self, controle: Controle) -> Controle:
        moment = controle.gestart_op or datetime.now()
        cursor = self._verbinding.execute(
            "INSERT INTO controles (ph_code, order_referentie, medewerker, gestart_op,"
            " regels, kratten, actieve_krat, is_deellevering, levering_referentie)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                controle.ph_code,
                controle.order_referentie,
                controle.medewerker,
                moment.isoformat(timespec="seconds"),
                _regels_naar_json(controle.regels),
                controle.kratten,
                controle.actieve_krat,
                int(controle.is_deellevering),
                controle.levering_referentie,
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
            " afwijking_notitie = ?, regels = ?, kratten = ?, actieve_krat = ?,"
            " is_deellevering = ?, levering_referentie = ?, zending_methode = ?,"
            " zending_vervoerder = ?, tracking = ?, labels_geprint = ?, label_fout = ?,"
            " kratverdeling_exact = ?, giftcards_overgeslagen = ? WHERE id = ?",
            (
                controle.medewerker,
                controle.akkoord_op.isoformat(timespec="seconds")
                if controle.akkoord_op
                else None,
                controle.akkoord_door,
                controle.afwijking_notitie,
                _regels_naar_json(controle.regels),
                controle.kratten,
                controle.actieve_krat,
                int(controle.is_deellevering),
                controle.levering_referentie,
                controle.zending_methode,
                controle.zending_vervoerder,
                json.dumps(controle.tracking, ensure_ascii=False),
                int(controle.labels_geprint),
                controle.label_fout,
                int(controle.kratverdeling_exact),
                controle.giftcards_overgeslagen,
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
            kratten=int(rij["kratten"] or 1),
            actieve_krat=int(rij["actieve_krat"] or 1),
            is_deellevering=bool(rij["is_deellevering"]),
            levering_referentie=rij["levering_referentie"] or "",
            zending_methode=rij["zending_methode"] or "",
            zending_vervoerder=rij["zending_vervoerder"] or "",
            tracking=json.loads(rij["tracking"] or "[]"),
            labels_geprint=bool(rij["labels_geprint"]),
            label_fout=rij["label_fout"] or "",
            kratverdeling_exact=bool(rij["kratverdeling_exact"]),
            giftcards_overgeslagen=int(rij["giftcards_overgeslagen"] or 0),
        )

    def leveringen(self, ph_code: str) -> list[Controle]:
        """Alle akkoord gegeven leveringen van een PH, oudste eerst."""
        rijen = self._verbinding.execute(
            "SELECT * FROM controles WHERE ph_code = ? AND akkoord_op IS NOT NULL"
            " ORDER BY id",
            (ph_code,),
        ).fetchall()
        return [self._naar_controle(rij) for rij in rijen]

    # -- labels ----------------------------------------------------------

    def bewaar_labels(
        self, controle_id: int, ph_code: str, labels: list[Label], eerste_krat: int = 1
    ) -> None:
        """Bewaar de labelbestanden, zodat opnieuw printen geen nieuw label kost."""
        moment = _nu()
        self._verbinding.executemany(
            "INSERT INTO labels (controle_id, ph_code, krat, parcel_id, mime_type,"
            " tracking_number, bestand_base64, gemaakt_op) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    controle_id,
                    ph_code,
                    eerste_krat + index,
                    label.parcel_id,
                    label.mime_type,
                    label.tracking_number,
                    label.file_base64,
                    moment,
                )
                for index, label in enumerate(labels)
            ],
        )
        self._verbinding.commit()

    def labels(self, controle_id: int) -> list[Label]:
        rijen = self._verbinding.execute(
            "SELECT * FROM labels WHERE controle_id = ? ORDER BY krat, id", (controle_id,)
        ).fetchall()
        return [
            Label(
                parcel_id=rij["parcel_id"],
                mime_type=rij["mime_type"],
                file_base64=rij["bestand_base64"],
                tracking_number=rij["tracking_number"],
            )
            for rij in rijen
        ]

    def label_kratten(self, controle_id: int) -> dict[int, str]:
        """Per krat het trackingnummer, voor op de pakbon."""
        rijen = self._verbinding.execute(
            "SELECT krat, tracking_number FROM labels WHERE controle_id = ? ORDER BY krat",
            (controle_id,),
        ).fetchall()
        return {int(rij["krat"]): rij["tracking_number"] for rij in rijen}

    # -- observaties -----------------------------------------------------

    def observatie(self, ph_code: str) -> Observatie | None:
        rij = self._verbinding.execute(
            "SELECT * FROM ph_observaties WHERE ph_code = ?", (ph_code,)
        ).fetchone()
        return self._naar_observatie(rij) if rij else None

    def observaties(self) -> dict[str, Observatie]:
        rijen = self._verbinding.execute("SELECT * FROM ph_observaties").fetchall()
        return {rij["ph_code"]: self._naar_observatie(rij) for rij in rijen}

    def noteer_waarneming(
        self,
        ph_code: str,
        *,
        order_referentie: str,
        vingerafdruk: str,
        is_compleet: bool,
        moment: datetime,
    ) -> Observatie:
        """Werk bij wat we van deze PH zien; houdt vast wanneer er iets veranderde."""
        bestaand = self.observatie(ph_code)

        if bestaand is None:
            observatie = Observatie(
                ph_code=ph_code,
                eerst_gezien=moment,
                laatst_gewijzigd=moment,
                vingerafdruk=vingerafdruk,
                compleet_sinds=moment if is_compleet else None,
                order_referentie=order_referentie,
            )
        else:
            veranderd = bestaand.vingerafdruk != vingerafdruk
            nieuwe_order = bestaand.order_referentie != order_referentie
            observatie = Observatie(
                ph_code=ph_code,
                # Andere order in dezelfde PH = een nieuwe klok.
                eerst_gezien=moment if nieuwe_order else bestaand.eerst_gezien,
                laatst_gewijzigd=moment if (veranderd or nieuwe_order) else bestaand.laatst_gewijzigd,
                vingerafdruk=vingerafdruk,
                compleet_sinds=(
                    (moment if (bestaand.compleet_sinds is None or nieuwe_order) else bestaand.compleet_sinds)
                    if is_compleet
                    else None
                ),
                order_referentie=order_referentie,
            )

        self._verbinding.execute(
            "INSERT INTO ph_observaties (ph_code, order_referentie, eerst_gezien,"
            " laatst_gewijzigd, vingerafdruk, compleet_sinds) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(ph_code) DO UPDATE SET order_referentie = excluded.order_referentie,"
            " eerst_gezien = excluded.eerst_gezien, laatst_gewijzigd = excluded.laatst_gewijzigd,"
            " vingerafdruk = excluded.vingerafdruk, compleet_sinds = excluded.compleet_sinds",
            (
                observatie.ph_code,
                observatie.order_referentie,
                observatie.eerst_gezien.isoformat(timespec="seconds"),
                observatie.laatst_gewijzigd.isoformat(timespec="seconds"),
                observatie.vingerafdruk,
                observatie.compleet_sinds.isoformat(timespec="seconds")
                if observatie.compleet_sinds
                else None,
            ),
        )
        self._verbinding.commit()
        return observatie

    def _naar_observatie(self, rij: sqlite3.Row) -> Observatie:
        return Observatie(
            ph_code=rij["ph_code"],
            order_referentie=rij["order_referentie"],
            eerst_gezien=datetime.fromisoformat(rij["eerst_gezien"]),
            laatst_gewijzigd=datetime.fromisoformat(rij["laatst_gewijzigd"]),
            vingerafdruk=rij["vingerafdruk"],
            compleet_sinds=datetime.fromisoformat(rij["compleet_sinds"])
            if rij["compleet_sinds"]
            else None,
        )

    # -- onderzoeken -----------------------------------------------------

    def open_onderzoek(self, ph_code: str) -> Onderzoek | None:
        rij = self._verbinding.execute(
            "SELECT * FROM onderzoeken WHERE ph_code = ? AND opgelost_op IS NULL"
            " ORDER BY id DESC LIMIT 1",
            (ph_code,),
        ).fetchone()
        return self._naar_onderzoek(rij) if rij else None

    def open_onderzoeken(self) -> list[Onderzoek]:
        rijen = self._verbinding.execute(
            "SELECT * FROM onderzoeken WHERE opgelost_op IS NULL ORDER BY geopend_op"
        ).fetchall()
        return [self._naar_onderzoek(rij) for rij in rijen]

    def start_onderzoek(self, onderzoek: Onderzoek) -> Onderzoek:
        cursor = self._verbinding.execute(
            "INSERT INTO onderzoeken (ph_code, reden, notitie, geopend_op, geopend_door)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                onderzoek.ph_code,
                onderzoek.reden,
                onderzoek.notitie,
                onderzoek.geopend_op.isoformat(timespec="seconds"),
                onderzoek.geopend_door,
            ),
        )
        self._verbinding.commit()
        onderzoek.id = int(cursor.lastrowid)
        return onderzoek

    def rond_onderzoek_af(
        self, onderzoek_id: int, *, oplossing: str, door: str, moment: datetime
    ) -> None:
        self._verbinding.execute(
            "UPDATE onderzoeken SET opgelost_op = ?, opgelost_door = ?, oplossing = ?"
            " WHERE id = ?",
            (moment.isoformat(timespec="seconds"), door, oplossing, onderzoek_id),
        )
        self._verbinding.commit()

    def _naar_onderzoek(self, rij: sqlite3.Row) -> Onderzoek:
        return Onderzoek(
            id=int(rij["id"]),
            ph_code=rij["ph_code"],
            reden=rij["reden"],
            notitie=rij["notitie"],
            geopend_op=datetime.fromisoformat(rij["geopend_op"]),
            geopend_door=rij["geopend_door"],
            opgelost_op=datetime.fromisoformat(rij["opgelost_op"]) if rij["opgelost_op"] else None,
            opgelost_door=rij["opgelost_door"],
            oplossing=rij["oplossing"],
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
