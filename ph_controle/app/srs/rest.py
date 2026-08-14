"""REST-client voor de SRS-webservice.

De endpoints en veldnamen van SRS staan niet in code maar in een
mappingbestand (standaard `config/srs_rest.json`). Zo is de koppeling een
kwestie van dat bestand invullen aan de hand van de SRS-documentatie,
zonder de app aan te passen.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from ..config import Instellingen
from ..models import Controle, Ph, PhRegel
from .base import PhNietGevonden, SrsFout


def haal_pad(data: Any, pad: str, standaard: Any = None) -> Any:
    """Waarde ophalen via een pad als `order.klant.naam` of `regels[0].code`."""
    if not pad:
        return standaard
    huidig = data
    for deel in pad.replace("]", "").replace("[", ".").split("."):
        if deel == "":
            continue
        if isinstance(huidig, list):
            try:
                huidig = huidig[int(deel)]
            except (ValueError, IndexError):
                return standaard
        elif isinstance(huidig, dict):
            if deel not in huidig:
                return standaard
            huidig = huidig[deel]
        else:
            return standaard
    return standaard if huidig is None else huidig


def _int(waarde: Any) -> int:
    try:
        return int(float(waarde))
    except (TypeError, ValueError):
        return 0


class RestSrsClient:
    """Praat met SRS via HTTP volgens het mappingbestand."""

    def __init__(self, instellingen: Instellingen, mapping: dict | None = None):
        self.instellingen = instellingen
        self.mapping = mapping if mapping is not None else self._laad_mapping()
        if not instellingen.srs_base_url:
            raise SrsFout("SRS_BASE_URL is niet ingesteld.")
        self._client = httpx.Client(
            base_url=instellingen.srs_base_url.rstrip("/"),
            timeout=instellingen.srs_timeout,
            verify=instellingen.srs_verify_ssl,
            headers=self._headers(),
            auth=self._auth(),
        )

    def _laad_mapping(self) -> dict:
        pad = Path(self.instellingen.srs_mapping_bestand)
        if not pad.exists():
            raise SrsFout(f"Mappingbestand {pad} ontbreekt.")
        return json.loads(pad.read_text(encoding="utf-8"))

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        extra = self.mapping.get("headers", {})
        headers.update({str(k): str(v) for k, v in extra.items()})
        soort = self.instellingen.srs_auth_type.lower()
        if soort == "bearer" and self.instellingen.srs_token:
            headers["Authorization"] = f"Bearer {self.instellingen.srs_token}"
        elif soort == "header" and self.instellingen.srs_token:
            headers[self.instellingen.srs_api_key_header] = self.instellingen.srs_token
        return headers

    def _auth(self) -> tuple[str, str] | None:
        if self.instellingen.srs_auth_type.lower() == "basic":
            return (self.instellingen.srs_gebruiker, self.instellingen.srs_wachtwoord)
        return None

    # -- HTTP ------------------------------------------------------------

    def _endpoint(self, naam: str) -> dict:
        endpoints = self.mapping.get("endpoints", {})
        if naam not in endpoints:
            raise SrsFout(f"Endpoint '{naam}' ontbreekt in het mappingbestand.")
        return endpoints[naam]

    def _roep(self, naam: str, *, pad_waarden: dict | None = None, body: Any = None) -> Any:
        endpoint = self._endpoint(naam)
        pad = endpoint.get("path", "").format(**(pad_waarden or {}))
        methode = endpoint.get("method", "GET").upper()
        try:
            antwoord = self._client.request(
                methode,
                pad,
                params=endpoint.get("params"),
                json=body if methode != "GET" else None,
            )
        except httpx.HTTPError as fout:
            raise SrsFout(f"SRS niet bereikbaar ({naam}): {fout}") from fout

        if antwoord.status_code == 404:
            raise PhNietGevonden(f"SRS gaf 404 op {methode} {pad}.")
        if antwoord.status_code >= 400:
            raise SrsFout(
                f"SRS gaf {antwoord.status_code} op {methode} {pad}: {antwoord.text[:300]}"
            )
        if not antwoord.content:
            return {}
        try:
            return antwoord.json()
        except ValueError as fout:
            raise SrsFout(f"SRS gaf geen geldige JSON op {methode} {pad}.") from fout

    # -- Mapping ---------------------------------------------------------

    def _ph_uit_json(self, data: dict) -> Ph:
        velden = self.mapping.get("fields", {}).get("ph", {})
        regelveld = velden.get("regels", "lines")
        regels_json = haal_pad(data, regelveld, []) or []
        return Ph(
            code=str(haal_pad(data, velden.get("code", "code"), "")),
            order_referentie=str(haal_pad(data, velden.get("order_referentie", ""), "")),
            order_id=str(haal_pad(data, velden.get("order_id", ""), "")),
            klant=str(haal_pad(data, velden.get("klant", ""), "")),
            adres=str(haal_pad(data, velden.get("adres", ""), "")),
            postcode_plaats=str(haal_pad(data, velden.get("postcode_plaats", ""), "")),
            land=str(haal_pad(data, velden.get("land", ""), "") or "Nederland"),
            verzendmethode=str(haal_pad(data, velden.get("verzendmethode", ""), "")),
            opmerking=str(haal_pad(data, velden.get("opmerking", ""), "")),
            orderdatum=str(haal_pad(data, velden.get("orderdatum", ""), "")),
            srs_status=str(haal_pad(data, velden.get("srs_status", ""), "")),
            regels=tuple(self._regel_uit_json(r) for r in regels_json),
        )

    def _regel_uit_json(self, data: dict) -> PhRegel:
        velden = self.mapping.get("fields", {}).get("regel", {})
        return PhRegel(
            barcode=str(haal_pad(data, velden.get("barcode", "barcode"), "")),
            omschrijving=str(haal_pad(data, velden.get("omschrijving", ""), "")),
            aantal_besteld=_int(haal_pad(data, velden.get("aantal_besteld", ""), 0)),
            aantal_in_ph=_int(haal_pad(data, velden.get("aantal_in_ph", ""), 0)),
            sku=str(haal_pad(data, velden.get("sku", ""), "")),
            picklocatie=str(haal_pad(data, velden.get("picklocatie", ""), "")),
        )

    # -- SrsClient -------------------------------------------------------

    def lijst_phs(self) -> list[Ph]:
        data = self._roep("lijst_phs")
        wortel = self._endpoint("lijst_phs").get("collection", "")
        rijen = haal_pad(data, wortel, data) if wortel else data
        if not isinstance(rijen, list):
            raise SrsFout("Antwoord van SRS op lijst_phs is geen lijst.")
        return [self._ph_uit_json(rij) for rij in rijen]

    def haal_ph(self, code: str) -> Ph:
        data = self._roep("haal_ph", pad_waarden={"ph_code": code})
        wortel = self._endpoint("haal_ph").get("root", "")
        return self._ph_uit_json(haal_pad(data, wortel, data) if wortel else data)

    def meld_ph_gereed(self, ph: Ph, controle: Controle) -> str:
        sjabloon = self.mapping.get("afmelding", {})
        regelvelden = sjabloon.get("regel_velden", {})
        body = {
            **sjabloon.get("vaste_velden", {}),
            sjabloon.get("ph_veld", "locationCode"): ph.code,
            sjabloon.get("order_veld", "orderReference"): ph.order_referentie,
            sjabloon.get("medewerker_veld", "user"): controle.akkoord_door
            or controle.medewerker,
            sjabloon.get("regels_veld", "lines"): [
                {
                    regelvelden.get("barcode", "barcode"): r.barcode,
                    regelvelden.get("aantal", "quantity"): r.aantal_geteld,
                }
                for r in controle.regels
            ],
        }
        antwoord = self._roep("meld_ph_gereed", pad_waarden={"ph_code": ph.code}, body=body)
        referentie_veld = sjabloon.get("referentie_veld", "")
        referentie = haal_pad(antwoord, referentie_veld, "") if referentie_veld else ""
        return str(referentie or f"SRS-{ph.code}")

    def close(self) -> None:
        self._client.close()
