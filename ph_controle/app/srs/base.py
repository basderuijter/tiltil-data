"""Adapterlaag naar SRS.

De app praat alleen via dit protocol met SRS. Daaronder zit óf de mock
(demofixture, voor testen en trainen) óf de REST-client tegen de echte
SRS-webservice. Nieuwe koppelvorm = nieuwe implementatie, verder niets.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import Controle, Ph


class SrsFout(RuntimeError):
    """SRS is niet bereikbaar of geeft een fout terug."""


class PhNietGevonden(SrsFout):
    """De opgevraagde PH bestaat niet (meer) in SRS."""


@runtime_checkable
class SrsClient(Protocol):
    def lijst_phs(self) -> list[Ph]:
        """Alle PH-verzamellocaties met een lopende order."""

    def haal_ph(self, code: str) -> Ph:
        """Eén PH inclusief orderregels en wat er nu in ligt."""

    def meld_ph_gereed(self, ph: Ph, controle: Controle) -> str:
        """Meld in SRS dat de PH gecontroleerd en akkoord is.

        Retourneert de bevestiging/referentie van SRS. Na deze melding mogen
        de producten uit de PH gehaald worden.
        """
