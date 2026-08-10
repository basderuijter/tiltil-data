"""Pure logica: is een PH compleet, en mag de controle akkoord?

Bewust vrij van I/O zodat dit los te testen is en de regels op één plek staan.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import CONDITIE_LABELS, Controle, ControleRegel, Ph, PhRegel

# Statuscodes van een PH volgens SRS-voorraad (wat ligt er in de bak).
COMPLEET = "compleet"
INCOMPLEET = "incompleet"
TEVEEL = "teveel"
LEEG = "leeg"

STATUS_LABELS: dict[str, str] = {
    COMPLEET: "Compleet",
    INCOMPLEET: "Incompleet",
    TEVEEL: "Te veel in PH",
    LEEG: "Leeg",
}


@dataclass(frozen=True)
class PhStatus:
    """Uitkomst van de compleetheidscheck van een PH."""

    status: str
    aantal_besteld: int
    aantal_in_ph: int
    ontbrekend: tuple[PhRegel, ...] = ()
    overtollig: tuple[PhRegel, ...] = ()

    @property
    def is_compleet(self) -> bool:
        return self.status in (COMPLEET, TEVEEL)

    @property
    def mag_uit_ph(self) -> bool:
        """Alleen een volledig gevulde PH mag leeggehaald worden."""
        return self.status == COMPLEET

    @property
    def label(self) -> str:
        return STATUS_LABELS[self.status]

    @property
    def aantal_ontbrekend(self) -> int:
        return sum(r.tekort for r in self.ontbrekend)


def ph_status(ph: Ph) -> PhStatus:
    """Vergelijk wat besteld is met wat er in de PH ligt."""
    besteld = sum(r.aantal_besteld for r in ph.regels)
    aanwezig = sum(min(r.aantal_in_ph, r.aantal_besteld) + r.teveel for r in ph.regels)
    ontbrekend = tuple(r for r in ph.regels if r.tekort > 0)
    overtollig = tuple(r for r in ph.regels if r.teveel > 0)

    if aanwezig == 0 and besteld > 0:
        status = LEEG
    elif ontbrekend:
        status = INCOMPLEET
    elif overtollig:
        status = TEVEEL
    else:
        status = COMPLEET

    return PhStatus(
        status=status,
        aantal_besteld=besteld,
        aantal_in_ph=sum(r.aantal_in_ph for r in ph.regels),
        ontbrekend=ontbrekend,
        overtollig=overtollig,
    )


def controle_regels_uit_ph(ph: Ph) -> list[ControleRegel]:
    """Maak lege controleregels op basis van de orderregels in SRS."""
    return [
        ControleRegel(
            barcode=r.barcode,
            omschrijving=r.omschrijving,
            aantal_verwacht=r.aantal_besteld,
            sku=r.sku,
        )
        for r in ph.regels
    ]


@dataclass(frozen=True)
class ScanResultaat:
    status: str  # "geteld" | "te_veel" | "onbekend"
    barcode: str
    regel: ControleRegel | None = None
    melding: str = ""


def scan(controle: Controle, barcode: str) -> ScanResultaat:
    """Verwerk één scan. Telt nooit verder dan het bestelde aantal."""
    barcode = (barcode or "").strip()
    if not barcode:
        return ScanResultaat("onbekend", barcode, None, "Lege scan.")

    regel = controle.regel(barcode)
    if regel is None:
        return ScanResultaat(
            "onbekend", barcode, None, f"Barcode {barcode} hoort niet bij deze order."
        )

    if regel.aantal_geteld >= regel.aantal_verwacht:
        return ScanResultaat(
            "te_veel",
            barcode,
            regel,
            f"{regel.omschrijving}: alle {regel.aantal_verwacht} stuks zijn al geteld.",
        )

    regel.aantal_geteld += 1
    return ScanResultaat(
        "geteld",
        barcode,
        regel,
        f"{regel.omschrijving}: {regel.aantal_geteld}/{regel.aantal_verwacht}",
    )


@dataclass(frozen=True)
class AkkoordCheck:
    mag_akkoord: bool
    redenen: tuple[str, ...] = ()


def mag_akkoord(controle: Controle) -> AkkoordCheck:
    """Akkoord kan alleen als alles geteld is én alles in goede staat is."""
    redenen: list[str] = []

    if not controle.regels:
        redenen.append("Deze PH heeft geen orderregels.")

    for regel in controle.regels:
        if regel.aantal_geteld < regel.aantal_verwacht:
            redenen.append(
                f"{regel.omschrijving}: {regel.aantal_geteld} van "
                f"{regel.aantal_verwacht} gecontroleerd."
            )
        elif regel.aantal_geteld > regel.aantal_verwacht:
            redenen.append(
                f"{regel.omschrijving}: {regel.aantal_geteld} geteld, "
                f"{regel.aantal_verwacht} besteld."
            )
        if regel.conditie != "goed":
            redenen.append(
                f"{regel.omschrijving}: gemarkeerd als "
                f"{CONDITIE_LABELS[regel.conditie].lower()}."
            )

    return AkkoordCheck(mag_akkoord=not redenen, redenen=tuple(redenen))
