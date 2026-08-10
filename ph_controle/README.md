# PH-controle

Webapp voor het magazijn: laat op een plattegrond van de pigeonhole-hal zien
welke **PH** leeg is, welke gevuld wordt, welke klaarstaat en welke vastloopt.
Een PH controleer je scannend op aantallen én staat van de producten; na akkoord
meldt de app af in SRS — dat is het moment waarop de producten eruit mogen — en
print de pakbon.

```
SRS ─► hal / overzicht ─► scan PH-label ─► scannen ─► akkoord ─► pakbon
        │                                                 └─► afmelding naar SRS
        └─► vastlopers ─► onderzoek ─► opgelost
```

## Snel starten (demodata, zonder SRS)

```bash
cd ph_controle
python -m venv .venv && .venv/bin/pip install -r requirements.txt
SRS_BACKEND=mock .venv/bin/python -m uvicorn app.main:maak_app --factory --port 8000
```

Open http://localhost:8000/hal. De mock leest `fixtures/srs_demo.json`: complete
PH's, incomplete, een lege en één met te veel voorraad. Wil je de signalering
zien werken, draai dan `python scripts/demo_seed.py` — dat zet een paar
waarnemingen terug in de tijd zodat er 'let op' en 'vastloper' in beeld komen.

## Snelheid: waar zit de winst

Het SRS-proces is: pigeonhole leeghalen, elk artikel één keer scannen, dan
"scan oké". Deze app haalt daar de losse handelingen tussenuit:

| Ingreep | Wat het scheelt |
|---|---|
| **Scan om te openen** | Scan het PH-label, de orderreferentie of een artikel op het overzicht of de plattegrond; het juiste scherm opent meteen. Geen zoeken in een lijst. |
| **Controle start vanzelf** | Bij een complete PH staat het scanveld direct klaar (zolang je naam bekend is). Scheelt de stap 'controle starten'. |
| **Snelmodus: laatste scan = klaar** | De scan die de order compleet maakt, geeft zelf akkoord, meldt af in SRS en opent de pakbon. Bij enkelstuksorders is de hele controle dus één scan. Uit te zetten met de knop *Snelmodus* op het controlescherm. |
| **Cijfer + Enter** | Meerdere stuks van hetzelfde artikel: scan er één en typ het aantal (bijv. `3` + Enter). Ook een knop *alle 3* per regel. |
| **Sneltoetsen** | `F2` akkoord, `Esc` terug naar het scanveld, `Enter` tellen. Het scanveld pakt automatisch de focus terug. |
| **Volgende PH (F2)** | Na het printen wijst de pakbon meteen de eerstvolgende PH aan die klaarstaat; doorpakken zonder terug naar een lijst. |
| **Doorlooptijd zichtbaar** | Het overzicht toont de gemiddelde controletijd van vandaag, zodat je ziet of het echt sneller gaat. |

Zet Chrome op de pak-pc in kiosk-printing (`chrome --kiosk-printing --app=http://<server>:8000/hal`),
dan gaat de pakbon zonder printdialoog rechtstreeks naar de standaardprinter —
dat scheelt bij elke order nog een klik.

Snelmodus blijft veilig: de app geeft alleen zelf akkoord als élke regel volledig
geteld is *en* op "Goed" staat. Zodra je een afwijking aangeeft, stopt het
automatische pad en gaat de PH in onderzoek.

## Plattegrond van de hal

`/hal` toont de wanden met alle vakken, kleur per toestand: leeg/beschikbaar,
wordt gevuld, klaar om te controleren, in controle, let op, vastloper,
gecontroleerd. Klik een vak en je zit in de details. Het scherm ververst zichzelf
elke 15 seconden, dus het kan op een wandmonitor blijven staan.

De indeling wordt afgeleid uit de PH-codes die SRS teruggeeft (`G-PH.01`,
`K-PH.05`, …): per wand gegroepeerd, gaten in de nummering worden als lege
vakken getoond. Wil je de echte indeling vastleggen, zet dan `config/hal.json`
neer:

```json
{
  "kolommen": 10,
  "wanden": [
    {"naam": "Gang G", "prefix": "G-PH", "van": 1, "tot": 24, "cijfers": 2},
    {"naam": "Gang K", "prefix": "K-PH", "van": 1, "tot": 18}
  ]
}
```

PH's die SRS teruggeeft maar die niet in dat bestand staan, worden er alsnog
bij getoond — een order mag nooit onzichtbaar zijn door een verouderde
plattegrond.

## Vastlopers en onderzoek

SRS houdt geen historie bij van hoe lang een order in een PH ligt, dus de app
kijkt zelf mee: sinds wanneer is de PH gevuld, wanneer veranderde er voor het
laatst iets, en hoe lang staat een complete PH te wachten op controle.

Een PH krijgt **let op** na 4 uur en wordt **vastloper** na 24 uur, of eerder
als er 8 uur geen voortgang is (in te stellen met `PH_LETOP_UREN`,
`PH_VASTLOPER_UREN`, `PH_STILSTAND_UREN`). Verder wordt een PH altijd een
vastloper bij een gemelde afwijking, bij te veel voorraad in de bak en als de
afmelding naar SRS mislukt.

Vastlopers staan bovenaan het overzicht en in de lijst *Vraagt aandacht* onder
de plattegrond, met de reden erbij ("Geen voortgang in 9 uur. Ontbreekt: Schaal
keramiek L (0/1)"). Je zet zo'n PH in onderzoek met een notitie; hij blijft
zichtbaar tot iemand het onderzoek afrondt met wat er aan de hand was. Een
alsnog geslaagde controle rondt het onderzoek automatisch af.

## Draaien tegen de echte SRS

```bash
export SRS_BACKEND=rest
export SRS_BASE_URL=https://srs.example.nl
export SRS_AUTH_TYPE=basic        # none | basic | bearer | header
export SRS_USERNAME=... SRS_PASSWORD=...
.venv/bin/python -m uvicorn app.main:maak_app --factory --host 0.0.0.0 --port 8000
```

De endpoints en veldnamen van SRS staan **niet in de code** maar in
`config/srs_rest.json`. Dat bestand is nu met plausibele voorbeeldwaarden
ingevuld en moet nog tegen de SRS-webservicedocumentatie aan gelegd worden:

| Onderdeel | Wat je invult |
|---|---|
| `endpoints.lijst_phs` | call die alle open PH-locaties teruggeeft (+ `collection`: waar de lijst in het antwoord zit) |
| `endpoints.haal_ph` | call voor één PH; `{ph_code}` wordt ingevuld |
| `endpoints.meld_ph_gereed` | call die de PH als gecontroleerd afmeldt |
| `fields.ph` / `fields.regel` | veldnamen in het SRS-antwoord, als pad: `shipping.name`, `lines[0].barcode` |
| `afmelding` | hoe de afmeldbody eruitziet |

Controleer daarna `GET /gezondheid`: die geeft `srs: ok` en het aantal gevonden
PH's terug. Dat is meteen een goede check voor monitoring.

## Wat de app afdwingt

- Een PH is **compleet** als voor elke orderregel het bestelde aantal in de PH
  ligt. Ligt er te veel in, dan is de status *Te veel in PH* en mag de PH
  bewust nog niet leeg — dat moet eerst uitgezocht worden.
- Tijdens de controle telt elke scan één stuk; verder tellen dan besteld kan
  niet. Een barcode die niet bij de order hoort wordt geweigerd met een melding.
- **Akkoord kan alleen** als elke regel volledig geteld is *en* op "Goed" staat.
  Beschadigd, verkeerd artikel of ontbrekend blokkeert het akkoord.
- Bij akkoord gaat eerst de afmelding naar SRS. Mislukt die, dan blijft de
  controle openstaan, komt de PH in onderzoek en is er geen pakbon — nooit een
  pakbon zonder afmelding.
- Alles wordt gelogd (start, scans, correcties, afwijkingen, onderzoek, akkoord)
  in `data/controles.db`, zichtbaar als logboek onderaan het controlescherm.

## Instellingen

Alles via omgevingsvariabelen; zie `.env.example` voor de volledige lijst.
De belangrijkste: `SRS_BACKEND`, `SRS_BASE_URL`, `SRS_AUTH_TYPE`, `PH_DB`,
de drempels `PH_LETOP_UREN` / `PH_VASTLOPER_UREN` / `PH_STILSTAND_UREN`,
`PH_SNELMODUS`, `PH_AUTO_START` en de `BEDRIJF_*`-velden op de pakbon.

## Tests

```bash
cd ph_controle && .venv/bin/python -m pytest
```

De suite dekt de compleetheidsregels, de scan- en akkoordlogica, de signalering
van vastlopers, de plattegrond, de snelheidsingrepen (scan-to-open, auto-start,
akkoord via de API) en de REST-mapping met een nagebootste SRS-webservice.

## Structuur

```
app/completeness.py   compleetheids- en akkoordregels (pure logica)
app/signalen.py       let op / vastloper en de kleur van een vak (pure logica)
app/hal.py            plattegrond: wanden, vakken, lege plekken
app/service.py        het proces: openen, scannen, akkoord, onderzoek
app/srs/              koppeling: base (protocol), mock (fixture), rest (webservice)
app/store.py          SQLite: controles, waarnemingen, onderzoeken, audittrail
app/main.py           FastAPI-routes en JSON-API voor scanscherm en plattegrond
app/templates/        hal, overzicht, controlescherm, pakbon
config/srs_rest.json  endpoints en veldmapping van SRS
scripts/demo_seed.py  demodata verouderen om de signalering te tonen
```

## Nog open

- `config/srs_rest.json` invullen met de echte SRS-endpoints en veldnamen.
- Verzendlabel: de Sendcloud-stap kan aanhaken op het akkoordmoment in
  `PhService.geef_akkoord`, naast de pakbon.
