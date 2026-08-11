"""Webapp: PH-controle, hal-overzicht en pakbon.

Bediening is scannergericht: het invoerveld houdt focus, de scanner tikt de
barcode + Enter, de regel telt op. In snelmodus rondt de laatste scan de
controle helemaal af — akkoord, afmelding naar SRS en pakbon in één beweging.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .completeness import mag_akkoord
from .config import Instellingen, laad_instellingen
from .models import CONDITIE_LABELS, Controle, Ph
from .service import ControleFout, PhService
from .signalen import TOESTAND_LABELS
from .srs import maak_client
from .srs.base import PhNietGevonden, SrsClient, SrsFout
from .store import Opslag
from .verzending import Adres, PrintError, SendcloudError, StationError, Verzendservice

HIER = Path(__file__).resolve().parent
COOKIE_MEDEWERKER = "ph_medewerker"
COOKIE_SNELMODUS = "ph_snelmodus"
COOKIE_MAX = 60 * 60 * 24 * 30


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
                "per_krat": list(r.per_krat),
            }
            for r in controle.regels
        ],
        "totaal_verwacht": controle.totaal_verwacht,
        "totaal_geteld": controle.totaal_geteld,
        "mag_akkoord": check.mag_akkoord,
        "redenen": list(check.redenen),
        "is_akkoord": controle.is_akkoord,
        "kratten": controle.kratten,
        "actieve_krat": controle.actieve_krat,
        "is_deellevering": controle.is_deellevering,
        "referentie": controle.referentie,
    }


def maak_app(
    instellingen: Instellingen | None = None,
    srs: SrsClient | None = None,
    opslag: Opslag | None = None,
    verzending: Verzendservice | None = None,
) -> FastAPI:
    instellingen = instellingen or laad_instellingen()
    app = FastAPI(title="TILTIL PH-controle")
    app.mount("/static", StaticFiles(directory=HIER / "static"), name="static")
    templates = Jinja2Templates(directory=str(HIER / "templates"))

    app.state.instellingen = instellingen
    app.state.srs = srs or maak_client(instellingen)
    app.state.opslag = opslag or Opslag(instellingen.database)
    app.state.verzending = verzending or Verzendservice(instellingen.verzending)

    def dienst() -> PhService:
        return PhService(
            app.state.srs, app.state.opslag, instellingen, app.state.verzending
        )

    def medewerker_van(request: Request) -> str:
        return (request.cookies.get(COOKIE_MEDEWERKER) or "").strip()

    def snelmodus_van(request: Request) -> bool:
        keuze = request.cookies.get(COOKIE_SNELMODUS)
        if keuze is None:
            return instellingen.snelmodus
        return keuze == "1"

    def pagina(request: Request, sjabloon: str, **context) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            sjabloon,
            {
                "instellingen": instellingen,
                "medewerker": medewerker_van(request),
                "snelmodus": snelmodus_van(request),
                "conditie_labels": CONDITIE_LABELS,
                "toestand_labels": TOESTAND_LABELS,
                **context,
            },
        )

    def foutpagina(request: Request, boodschap: str, status: int = 400) -> HTMLResponse:
        antwoord = pagina(request, "fout.html", boodschap=boodschap)
        antwoord.status_code = status
        return antwoord

    # -- overzicht en hal ------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, melding: str = ""):
        service = dienst()
        try:
            regels = service.overzicht()
        except SrsFout as fout:
            return foutpagina(request, f"SRS niet bereikbaar: {fout}", status=502)
        return pagina(
            request,
            "index.html",
            regels=regels,
            stats=service.statistieken(regels),
            melding=melding,
        )

    @app.get("/hal", response_class=HTMLResponse)
    def hal(request: Request, melding: str = ""):
        service = dienst()
        try:
            wanden, regels = service.hal()
        except SrsFout as fout:
            return foutpagina(request, f"SRS niet bereikbaar: {fout}", status=502)
        return pagina(
            request,
            "hal.html",
            wanden=wanden,
            stats=service.statistieken(regels),
            vastlopers=[r for r in regels if r.signaal.vraagt_aandacht],
            melding=melding,
        )

    @app.get("/api/hal")
    def api_hal():
        """Toestand per vak — voor het automatisch verversen van de plattegrond."""
        try:
            wanden, _ = dienst().hal()
        except SrsFout as fout:
            return JSONResponse({"fout": str(fout)}, status_code=502)
        return JSONResponse(
            {
                "vakken": {
                    vak.code: {
                        "toestand": vak.toestand,
                        "order": getattr(vak.ph, "order_referentie", ""),
                        "signaal": getattr(vak.signaal, "niveau", "normaal"),
                        "leeftijd": getattr(vak.signaal, "leeftijd", ""),
                    }
                    for wand in wanden
                    for vak in wand.vakken
                }
            }
        )

    @app.post("/medewerker")
    def zet_medewerker(naam: str = Form(""), terug: str = Form("/")):
        antwoord = RedirectResponse(terug or "/", status_code=303)
        antwoord.set_cookie(COOKIE_MEDEWERKER, naam.strip(), max_age=COOKIE_MAX, httponly=False)
        return antwoord

    @app.post("/snelmodus")
    def zet_snelmodus(aan: str = Form(""), terug: str = Form("/")):
        antwoord = RedirectResponse(terug or "/", status_code=303)
        antwoord.set_cookie(COOKIE_SNELMODUS, "1" if aan == "1" else "0", max_age=COOKIE_MAX)
        return antwoord

    @app.post("/open")
    def open_via_scan(request: Request, zoekterm: str = Form(""), terug: str = Form("/")):
        """Scan een PH-label, orderreferentie of artikel en spring naar het scherm."""
        try:
            code = dienst().zoek(zoekterm)
        except SrsFout as fout:
            return foutpagina(request, f"SRS niet bereikbaar: {fout}", status=502)
        if code is None:
            melding = f"Niets gevonden voor '{zoekterm.strip()}'."
            return RedirectResponse(f"{terug or '/'}?melding={melding}", status_code=303)
        return RedirectResponse(f"/ph/{code}", status_code=303)

    # -- controlescherm --------------------------------------------------

    @app.get("/ph/{code}", response_class=HTMLResponse)
    def ph_detail(request: Request, code: str):
        service = dienst()
        try:
            ph, status, signaal, onderzoek = service.ph_met_signaal(code)
        except PhNietGevonden:
            return foutpagina(request, f"PH {code} bestaat niet in SRS.", status=404)
        except SrsFout as fout:
            return foutpagina(request, f"SRS niet bereikbaar: {fout}", status=502)

        controle = app.state.opslag.laatste_controle(code)
        naam = medewerker_van(request)

        # Scheelt een klik: bij een complete PH begint de controle meteen.
        if (
            instellingen.auto_start
            and naam
            and controle is None
            and status.mag_uit_ph
            and onderzoek is None
        ):
            controle = service.start_controle(code, naam)

        return pagina(
            request,
            "ph.html",
            ph=ph,
            status=status,
            signaal=signaal,
            onderzoek=onderzoek,
            controle=controle,
            controle_json=_controle_json(ph, controle) if controle else None,
            gebeurtenissen=app.state.opslag.gebeurtenissen(code, limiet=12),
        )

    @app.post("/ph/{code}/start")
    def start_controle(
        request: Request, code: str, naam: str = Form(""), deellevering: str = Form("")
    ):
        naam = (naam or medewerker_van(request)).strip()
        try:
            dienst().start_controle(code, naam, deellevering=deellevering == "1")
        except SrsFout as fout:
            return foutpagina(request, f"SRS niet bereikbaar: {fout}", status=502)
        antwoord = RedirectResponse(f"/ph/{code}", status_code=303)
        if naam:
            antwoord.set_cookie(COOKIE_MEDEWERKER, naam, max_age=COOKIE_MAX)
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

    @app.post("/api/ph/{code}/krat")
    async def api_krat(request: Request, code: str):
        """Wisselen van krat: alles wat hierna gescand wordt gaat in deze doos."""
        gegevens = await request.json()
        service = dienst()
        try:
            controle = service.zet_krat(
                code, int(gegevens.get("krat", 1)), medewerker_van(request)
            )
            ph = service.haal_ph(code)
        except (ControleFout, ValueError) as fout:
            return JSONResponse({"fout": str(fout)}, status_code=409)
        except SrsFout as fout:
            return JSONResponse({"fout": str(fout)}, status_code=502)
        return JSONResponse({"controle": _controle_json(ph, controle)})

    @app.post("/api/ph/{code}/akkoord")
    def api_akkoord(request: Request, code: str):
        """Akkoord vanuit het scanscherm — gebruikt door snelmodus en F2."""
        naam = medewerker_van(request)
        try:
            resultaat = dienst().geef_akkoord(code, naam)
        except ControleFout as fout:
            return JSONResponse({"fout": str(fout)}, status_code=409)
        except SrsFout as fout:
            return JSONResponse({"fout": str(fout)}, status_code=502)
        return JSONResponse(
            {
                "pakbon_url": f"/ph/{code}/pakbon?direct=1",
                "srs_referentie": resultaat.srs_referentie,
                "seconden": round(resultaat.doorlooptijd_seconden),
                "volgende_ph": resultaat.volgende_ph,
                "labels": resultaat.labels,
                "label_fout": resultaat.label_fout,
            }
        )

    # -- akkoord, afwijking en onderzoek ---------------------------------

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
            antwoord.set_cookie(COOKIE_MEDEWERKER, naam, max_age=COOKIE_MAX)
        return antwoord

    @app.post("/ph/{code}/afwijking")
    def afwijking(request: Request, code: str, notitie: str = Form("")):
        try:
            dienst().meld_afwijking(code, notitie, medewerker_van(request))
        except ControleFout as fout:
            return foutpagina(request, str(fout), status=409)
        return RedirectResponse(f"/ph/{code}", status_code=303)

    @app.post("/ph/{code}/onderzoek")
    def onderzoek_starten(
        request: Request, code: str, reden: str = Form(""), notitie: str = Form("")
    ):
        dienst().start_onderzoek(
            code,
            reden=reden or "Handmatig gemeld",
            notitie=notitie,
            medewerker=medewerker_van(request),
        )
        return RedirectResponse(f"/ph/{code}", status_code=303)

    @app.post("/ph/{code}/onderzoek/afronden")
    def onderzoek_afronden(request: Request, code: str, oplossing: str = Form("")):
        try:
            dienst().rond_onderzoek_af(
                code, oplossing=oplossing, medewerker=medewerker_van(request)
            )
        except ControleFout as fout:
            return foutpagina(request, str(fout), status=409)
        return RedirectResponse(f"/ph/{code}", status_code=303)

    # -- labels ----------------------------------------------------------

    @app.post("/ph/{code}/label/opnieuw")
    def label_opnieuw(request: Request, code: str):
        try:
            dienst().print_label_opnieuw(code, medewerker_van(request))
        except ControleFout as fout:
            return foutpagina(request, str(fout), status=409)
        return RedirectResponse(f"/ph/{code}/pakbon", status_code=303)

    @app.post("/ph/{code}/label/alsnog")
    def label_alsnog(request: Request, code: str):
        try:
            dienst().maak_label_alsnog(code, medewerker_van(request))
        except ControleFout as fout:
            return foutpagina(request, str(fout), status=409)
        except SrsFout as fout:
            return foutpagina(request, f"SRS niet bereikbaar: {fout}", status=502)
        return RedirectResponse(f"/ph/{code}/pakbon", status_code=303)

    # -- zending zonder PH ------------------------------------------------

    @app.get("/verzending", response_class=HTMLResponse)
    def verzending_scherm(request: Request, melding: str = "", gelukt: str = ""):
        return pagina(
            request,
            "verzending.html",
            melding=melding,
            gelukt=gelukt,
            verzending_actief=app.state.instellingen.verzending.actief,
        )

    @app.post("/verzending")
    def verzending_maken(
        request: Request,
        ontvanger: str = Form(""),
        adres: str = Form(""),
        postcode: str = Form(""),
        plaats: str = Form(""),
        landcode: str = Form("NL"),
        bedrijf: str = Form(""),
        gewicht: float = Form(1.0),
        methode: str = Form(""),
        aantal: int = Form(1),
        referentie: str = Form(""),
    ):
        try:
            resultaat = dienst().losse_zending(
                Adres(
                    naam=ontvanger.strip(),
                    adres=adres.strip(),
                    postcode=postcode.strip(),
                    plaats=plaats.strip(),
                    landcode=(landcode or "NL").strip().upper(),
                    bedrijf=bedrijf.strip(),
                    gewicht_kg=gewicht,
                ),
                methode_code=methode.strip(),
                aantal=aantal,
                referentie=referentie.strip(),
                medewerker=medewerker_van(request),
            )
        except (SendcloudError, PrintError, StationError, ValueError) as fout:
            return RedirectResponse(f"/verzending?melding={fout}", status_code=303)

        gelukt = f"{len(resultaat.labels)} label(s) gemaakt"
        if resultaat.tracking:
            gelukt += f" · {', '.join(resultaat.tracking)}"
        if resultaat.printfout:
            gelukt += f" · niet geprint: {resultaat.printfout}"
        return RedirectResponse(f"/verzending?gelukt={gelukt}", status_code=303)

    # -- pakbon ----------------------------------------------------------

    @app.get("/ph/{code}/pakbon", response_class=HTMLResponse)
    def pakbon(request: Request, code: str, direct: int = 0):
        service = dienst()
        try:
            ph, controle, kratten = service.pakbon_gegevens(code)
            volgende = service.volgende_klaar(behalve=code)
            openstaand = service.nog_te_leveren(code) if controle.is_deellevering else []
        except ControleFout as fout:
            return foutpagina(request, str(fout), status=409)
        except SrsFout as fout:
            return foutpagina(request, f"SRS niet bereikbaar: {fout}", status=502)
        return pagina(
            request,
            "pakbon.html",
            ph=ph,
            controle=controle,
            kratten=kratten,
            openstaand=openstaand,
            volgende=volgende,
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
