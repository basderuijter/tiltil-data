"""Backdate een paar PH's in de demodatabase, zodat je de signalering ziet werken.

De app kent de leeftijd van een PH uit eigen waarnemingen; in een verse
demo-omgeving is dus alles net binnen. Dit script zet een paar waarnemingen
terug in de tijd zodat je 'let op' en 'vastloper' in beeld krijgt.

    python scripts/demo_seed.py [pad/naar/controles.db]
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import laad_instellingen  # noqa: E402
from app.store import Opslag  # noqa: E402

# PH-code -> (uren geleden voor het eerst gezien, uren geleden laatste verandering)
BACKDATE = {
    "G-PH.02": (31, 27),  # loopt vast: al ruim een dag incompleet
    "G-PH.07": (6, 6),  # let op: vanochtend begonnen, sindsdien niets
    "K-PH.06": (9, 9),  # vastloper: geen voortgang
}


def main(pad: str | None = None) -> None:
    instellingen = laad_instellingen()
    opslag = Opslag(pad or instellingen.database)
    nu = datetime.now()

    for code, (leeftijd, stilstand) in BACKDATE.items():
        observatie = opslag.observatie(code)
        if observatie is None:
            print(f"{code}: nog niet waargenomen — open eerst het overzicht in de app.")
            continue
        opslag._verbinding.execute(
            "UPDATE ph_observaties SET eerst_gezien = ?, laatst_gewijzigd = ? WHERE ph_code = ?",
            (
                (nu - timedelta(hours=leeftijd)).isoformat(timespec="seconds"),
                (nu - timedelta(hours=stilstand)).isoformat(timespec="seconds"),
                code,
            ),
        )
        print(f"{code}: {leeftijd} uur oud, {stilstand} uur zonder voortgang.")

    opslag._verbinding.commit()
    opslag.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
