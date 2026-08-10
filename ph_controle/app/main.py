"""Webapp: PH-controle en pakbon.

Bediening is scannergericht: het invoerveld houdt focus, de scanner tikt de
barcode + Enter, de regel telt op. Muis is alleen nodig voor correcties.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .completeness import mag_akkoord, ph_status
from .config import Instellingen, laad_instellingen
from .models import CONDITIE_LABELS, Controle, Ph
from .service import ControleFout, PhService
from .srs import maak_client
from .srs.base import PhNietGevonden, SrsClient, SrsFout
from .store import Opslag

HIER = Path(__file__).resolve().parent
COOKIE_MEDEWERKER = "ph_medewerker"


def _controle_json(ph: Ph, controle: Controle) -> dict:
    check = mag_akkoord(controle)
    return {
        "ph": ph.code,
        "order": ph.order_referentie,
        "regels": [
            {
                "barcode": r.barcode,
                "omschrijving": r.omschrijving,
                "sku": r.sku,
                "aantal_verwacht": r.aantal_verwacht,
                "aantal_geteld": r.aantal_geteld,
                "conditie": r.conditie,
                "conditie_label": CONDITIE_LABELS[r.conditie],
                "notitie": r.notitie,
                "is_akkoord": r.is_akkoord,
            }
            for r in controle.regels
        ],
        "totaal_verwacht": controle.totaal_verwacht,
        "totaal_geteld": controle.totaal_geteld,
        "mag_akkoord": check.mag_akkoord,
        "redenen": list(check.redenen),
        "is_akkoord": controle.is_akkoord,
    }


def maak_app(
    instellingen: Instellingen | None = None,
    srs: SrsClient | None = None,
    opslag: Opslag | None = None,
) -> FastAPI:
    instellingen = instellingen or laad_instellingen()
    app = FastAPI(title="TILTIL PH-controle")
    app.mount("/static", StaticFiles(directory=HIER / "static"), name="static")
    templates = Jinja2Templates(directory=str(HIER / "templates"))

    app.state.instellingen = instellingen
    app.state.srs = srs or maak_client(instellingen)
    app.state.opslag = opslag or Opslag(instellingen.database)

    def dienst() -> PhService:
        return PhService(app.state.srs, app.state.opslag)

    def medewerker_van(request: Request) -> str:
        return (request.cookies.get(COOKIE_MEDEWERKER) or "").strip()

    def pagina(request: Request, sjabloon: str, **context) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            sjabloon,
            {
                "instellingen": instellingen,
                "medewerker": medewerker_van(request),
                "conditie_labels": CONDITIE_LABELS,
                **context,
            },
        )

    def foutpagina(request: Request, boodschap: str, status: int = 400) -> HTMLResponse:
        antwoord = pagina(request, "fout.html", boodschap=boodschap)
        antwoord.status_code = status
        return antwoord

    # -- overzicht -------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        try:
            regels = dienst().overzicht()
        except SrsFout as fout:
            return foutpagina(request, f"SRS niet bereikbaar: {fout}", status=502)
        return pagina(
            request,
            "index.html",
            regels=regels,
            aantal_klaar=sum(1 for r in regels if r.status.mag_uit_ph and not r.is_afgerond),
        )

    @app.post("/medewerker")
    def zet_medewerker(naam: str = Form(""), terug: str = Form("/")):
        antwoord = RedirectResponse(terug or "/", status_code=303)
        antwoord.set_cookie(
            COOKIE_MEDEWERKER, naam.strip(), max_age=60 * 60 * 24 * 30, httponly=False
        )
        return antwoord

    # -- controlescherm --------------------------------------------------

    @app.get("/ph/{code}", response_class=HTMLResponse)
    def ph_detail(request: Request, code: str):
        service = dienst()
        try:
            ph = service.haal_ph(code)
        except PhNietGevonden:
            return foutpagina(request, f"PH {code} bestaat niet in SRS.", status=404)
        except SrsFout as fout:
            return foutpagina(request, f"SRS niet bereikbaar: {fout}", status=502)

        controle = app.state.opslag.laatste_controle(code)
        status = ph_status(ph)
        return pagina(
            request,
            "ph.html",
            ph=ph,
            status=status,
            controle=controle,
            controle_json=_controle_json(ph, controle) if controle else None,
            gebeurtenissen=app.state.opslag.gebeurtenissen(code, limiet=12),
        )

    @app.post("/ph/{code}/start")
    def start_controle(request: Request, code: str, naam: str = Form("")):
        naam = (naam or medewerker_van(request)).strip()
        try:
            dienst().start_controle(code, naam)
        except SrsFout as fout:
            return foutpagina(request, f"SRS niet bereikbaar: {fout}", status=502)
        antwoord = RedirectResponse(f"/ph/{code}", status_code=303)
        if naam:
            antwoord.set_cookie(COOKIE_MEDEWERKER, naam, max_age=60 * 60 * 24 * 30)
        return antwoord

    # -- scannen en corrigeren (JSON) ------------------------------------

    @app.post("/api/ph/{code}/scan")
    async def api_scan(request: Request, code: str):
        gegevens = await request.json()
        service = dienst()
        try:
            resultaat = service.scan_barcode(
                code, str(gegevens.get("barcode", "")), medewerker_van(request)
            )
            ph = service.haal_ph(code)
            controle = app.state.opslag.open_controle(code)
        except ControleFout as fout:
            return JSONResponse({"fout": str(fout)}, status_code=409)
        except SrsFout as fout:
            return JSONResponse({"fout": str(fout)}, status_code=502)
        return JSONResponse(
            {
                "scan": {
                    "status": resultaat.status,
                    "barcode": resultaat.barcode,
                    "melding": resultaat.melding,
                },
                "controle": _controle_json(ph, controle),
            }
        )

    @app.post("/api/ph/{code}/regel")
    async def api_regel(request: Request, code: str):
        gegevens = await request.json()
        service = dienst()
        aantal = gegevens.get("aantal")
        try:
            controle = service.zet_regel(
                code,
                str(gegevens.get("barcode", "")),
                aantal=int(aantal) if aantal is not None else None,
                conditie=gegevens.get("conditie"),
                notitie=gegevens.get("notitie"),
                medewerker=medewerker_van(request),
            )
            ph = service.haal_ph(code)
        except (ControleFout, ValueError) as fout:
            return JSONResponse({"fout": str(fout)}, status_code=409)
        except SrsFout as fout:
            return JSONResponse({"fout": str(fout)}, status_code=502)
        return JSONResponse({"controle": _controle_json(ph, controle)})

    # -- akkoord en afwijking --------------------------------------------

    @app.post("/ph/{code}/akkoord")
    def akkoord(request: Request, code: str, naam: str = Form("")):
        naam = (naam or medewerker_van(request)).strip()
        try:
            dienst().geef_akkoord(code, naam)
        except ControleFout as fout:
            return foutpagina(request, str(fout), status=409)
        except SrsFout as fout:
            return foutpagina(
                request,
                f"Akkoord niet doorgegeven aan SRS: {fout}. De controle staat nog open, "
                "probeer het opnieuw.",
                status=502,
            )
        antwoord = RedirectResponse(f"/ph/{code}/pakbon?direct=1", status_code=303)
        if naam:
            antwoord.set_cookie(COOKIE_MEDEWERKER, naam, max_age=60 * 60 * 24 * 30)
        return antwoord

    @app.post("/ph/{code}/afwijking")
    def afwijking(request: Request, code: str, notitie: str = Form("")):
        try:
            dienst().meld_afwijking(code, notitie, medewerker_van(request))
        except ControleFout as fout:
            return foutpagina(request, str(fout), status=409)
        return RedirectResponse(f"/ph/{code}", status_code=303)

    # -- pakbon ----------------------------------------------------------

    @app.get("/ph/{code}/pakbon", response_class=HTMLResponse)
    def pakbon(request: Request, code: str, direct: int = 0):
        try:
            ph, controle = dienst().pakbon_gegevens(code)
        except ControleFout as fout:
            return foutpagina(request, str(fout), status=409)
        except SrsFout as fout:
            return foutpagina(request, f"SRS niet bereikbaar: {fout}", status=502)
        return pagina(
            request,
            "pakbon.html",
            ph=ph,
            controle=controle,
            automatisch_printen=bool(direct),
        )

    # -- techniek --------------------------------------------------------

    @app.get("/gezondheid")
    def gezondheid():
        status = {"app": "ok", "srs_backend": instellingen.srs_backend}
        try:
            status["aantal_phs"] = len(app.state.srs.lijst_phs())
            status["srs"] = "ok"
        except Exception as fout:  # noqa: BLE001 - status mag nooit crashen
            status["srs"] = f"fout: {fout}"
            return JSONResponse(status, status_code=503)
        return JSONResponse(status)

    return app
