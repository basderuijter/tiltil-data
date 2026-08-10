# PH-controle

Webapp voor het magazijn: laat zien of een **PH** (verzamellocatie van een
webshoporder) compleet is, laat de order scannend controleren op aantallen én
staat van de producten, en print na akkoord de pakbon. Bij akkoord meldt de app
de PH af in SRS — dat is het moment waarop de producten uit de PH mogen.

```
SRS  ──►  overzicht PH's  ──►  controle (scannen + staat)  ──►  akkoord  ──►  pakbon printen
                                                                   └──►  afmelding terug naar SRS
```

## Snel starten (demodata, zonder SRS)

```bash
cd ph_controle
python -m venv .venv && .venv/bin/pip install -r requirements.txt
SRS_BACKEND=mock .venv/bin/python -m uvicorn app.main:maak_app --factory --port 8000
```

Open http://localhost:8000. De mock leest `fixtures/srs_demo.json`: één complete
PH, één met ontbrekende regels, één lege en één die klaar staat. Handig om mee
te oefenen en om nieuwe medewerkers in te werken.

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
  controle openstaan en is er geen pakbon — nooit een pakbon zonder afmelding.
- Alles wordt gelogd (start, scans, correcties, afwijkingen, akkoord) in
  `data/controles.db`, zichtbaar als logboek onderaan het controlescherm.

## Bediening in het magazijn

Het scanveld houdt automatisch focus, dus een USB-scanner die als toetsenbord
werkt (barcode + Enter) werkt zonder klikken. Met `−` / `+` corrigeer je een
aantal handmatig, met de kolom *Staat* leg je vast dat een product beschadigd
of verkeerd is. Klopt er iets niet, dan legt *Afwijking melden* dat vast en
blijft de PH openstaan voor opvolging.

De pakbon opent na akkoord direct in het printvenster (A4, huisstijl). Vanaf
het overzicht kun je de pakbon van een afgemelde PH altijd opnieuw printen.

## Instellingen

Alles via omgevingsvariabelen; zie `.env.example` voor de volledige lijst.
De belangrijkste: `SRS_BACKEND`, `SRS_BASE_URL`, `SRS_AUTH_TYPE`, `PH_DB`
(pad naar de SQLite-database) en de `BEDRIJF_*`-velden die op de pakbon komen.

## Tests

```bash
cd ph_controle && .venv/bin/python -m pytest
```

De suite dekt de compleetheidsregels, de scan- en akkoordlogica, de webflow van
scannen tot pakbon en de REST-mapping (met een nagebootste SRS-webservice).

## Structuur

```
app/completeness.py   compleetheids- en akkoordregels (pure logica)
app/service.py        het proces: starten, scannen, corrigeren, akkoord
app/srs/              koppeling: base (protocol), mock (fixture), rest (webservice)
app/store.py          SQLite: controles + audittrail
app/main.py           FastAPI-routes en JSON-API voor het scanscherm
app/templates/        overzicht, controlescherm, pakbon
config/srs_rest.json  endpoints en veldmapping van SRS
```

## Nog open

- `config/srs_rest.json` invullen met de echte SRS-endpoints en veldnamen.
- Verzendlabel: de Sendcloud-stap kan aanhaken op het akkoordmoment in
  `PhService.geef_akkoord`, naast de pakbon.
