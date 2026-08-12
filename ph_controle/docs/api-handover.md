# Handoff: webservices en API's rond de PH-controle

Dit document beschrijft elke koppeling die de PH-app gebruikt of nodig heeft,
zodat een andere agent er direct mee kan bouwen zonder alles opnieuw uit te
zoeken. Per onderdeel staat erbij hoe zeker we van iets zijn:

| Merk | Betekenis |
|---|---|
| **Getest** | werkend gezien of gedekt door tests in deze repo |
| **Aangenomen** | plausibel gemodelleerd op documentatie, nog niet tegen de echte dienst gedraaid |
| **Onbekend** | placeholder; moet geverifieerd worden voordat het live gaat |

De werkende code staat in `ph_controle/app/`. Losse feiten zonder code
erbij zijn onderaan per onderdeel samengevat.

---

## 1. SRS — bron van orders en voorraad in de pigeonholes

SRS is het retailsysteem waarin de webshoporders en de voorraad staan. Een PH
(pigeonhole) is een verzamelvak; SRS weet welke order in welk vak verzameld
wordt en hoeveel stuks er per regel in liggen.

**Status: Onbekend.** De endpoints hieronder zijn plausibele placeholders. Er
was geen documentatie en er zijn geen credentials beschikbaar geweest, dus
niets hiervan is tegen een echte SRS-instantie gedraaid.

### Wat de app van SRS nodig heeft

Precies drie dingen — meer niet:

| Actie | Waarvoor |
|---|---|
| lijst van open PH's | plattegrond en overzicht |
| één PH met orderregels | controlescherm |
| PH gecontroleerd afmelden | vrijgeven, zodat de producten eruit mogen |

### Hoe het gekoppeld is

De koppeling zit achter een adapter (`app/srs/base.py`), met twee
implementaties: `mock` (JSON-fixture, voor draaien en testen) en `rest` (de
echte webservice). Endpoints en veldnamen staan **niet in code** maar in
`config/srs_rest.json`, met dotted paths (`shipping.name`, `lines[0].barcode`).
Een nieuwe SRS-versie of ander veldnaam = configbestand aanpassen, geen code.

```json
{
  "endpoints": {
    "lijst_phs":      {"method": "GET",  "path": "/api/v1/picklocations",
                       "params": {"type": "PH", "status": "open"}, "collection": "data"},
    "haal_ph":        {"method": "GET",  "path": "/api/v1/picklocations/{ph_code}", "root": "data"},
    "meld_ph_gereed": {"method": "POST", "path": "/api/v1/picklocations/{ph_code}/complete"}
  },
  "fields": {
    "ph":    {"code": "locationCode", "order_referentie": "orderReference",
              "klant": "shipping.name", "regels": "lines", "...": "..."},
    "regel": {"barcode": "barcode", "aantal_besteld": "quantityOrdered",
              "aantal_in_ph": "quantityInLocation", "...": "..."}
  },
  "afmelding": {
    "ph_veld": "locationCode", "medewerker_veld": "checkedBy", "regels_veld": "lines",
    "regel_velden": {"barcode": "barcode", "aantal": "quantity"},
    "vaste_velden": {"status": "checked"}, "referentie_veld": "data.reference"
  }
}
```

Auth via `SRS_AUTH_TYPE`: `none` | `basic` (user/wachtwoord) | `bearer` (token)
| `header` (token in een zelfgekozen header, standaard `X-Api-Key`).

### Gedragsafspraken

- **404 = PH bestaat niet** (`PhNietGevonden`); elke andere 4xx/5xx en elke
  netwerkfout wordt `SrsFout`. Beide moeten in de UI verschillend landen: het
  eerste is een verkeerde scan, het tweede een storing.
- **De afmelding stuurt alleen getelde aantallen**, niet de bestelde. Daarmee
  werkt dezelfde call ook voor een deellevering.
- **SRS levert geen historie.** Er komt geen "sinds wanneer ligt dit hier" mee.
  De app houdt dat zelf bij (zie §5, waarnemingen) — bouw daar niet omheen door
  timestamps uit SRS te verwachten.
- `GET /gezondheid` op de app geeft `srs: ok` plus het aantal gevonden PH's.
  Bruikbaar als smoke test na het invullen van de mapping.

### Nog te doen

Het configbestand invullen aan de hand van de echte SRS-webservicedocumentatie
en één PH end-to-end doorlopen. Zolang dat niet gebeurd is, draait alles op
`SRS_BACKEND=mock`.

---

## 2. Sendcloud v3 — verzendlabels

**Status: Aangenomen tot Getest**, per punt aangegeven. Dit deel is het best
uitgezocht; de client is gedekt door 17 tests met gemockte HTTP-antwoorden
(`tests/test_sendcloud.py`).

- Basis-URL: `https://panel.sendcloud.sc/api`
- Auth: HTTP basic met public key als gebruiker en secret key als wachtwoord.
- `integration_id`: de Shopify-integratie in Sendcloud (Settings > Integrations,
  het id staat in de URL).

### Endpoints

| Doel | Call | Status |
|---|---|---|
| Label voor één pakket | `POST /v3/orders/create-label-sync` | Aangenomen |
| Labels voor meerdere pakketten | `POST /v3/orders/create-labels-async` | Aangenomen |
| Zending ophalen (labels na async) | `GET /v3/shipments/{id}` | Aangenomen |
| Losse zending aanmaken | `POST /v3/shipments` | **Onbekend** (`VERIFY:` in code) |
| Order zoeken | `GET /v3/orders?order_number=` | **Onbekend**, valt terug op `GET /v2/parcels?order_number=` |
| Orderdetail (artikelregels) | `GET /v3/orders/{id}` | Aangenomen |
| Verzendopties | `POST /v3/shipping-options` | **Onbekend**, valt terug op een JSON-bestand |
| Contracten | `GET /v3/contracts?is_active=true` | Aangenomen, alleen nodig bij inrichten |

### Feiten die tijd besparen

1. **De verzendmethode is een tekstcode, geen nummer.** In elke aanvraag:

   ```json
   "ship_with": {
     "type": "shipping_option_code",
     "properties": {"shipping_option_code": "postnl:standard", "contract_id": null}
   }
   ```

   `contract_id` mag leeg: Sendcloud pakt dan het standaardcontract per
   vervoerder. Codes zien eruit als `postnl:standard`, `postnl:letterbox`,
   `postnl:service_point`, `dhl:parcel_connect`.

2. **Eén pakket geeft het label meteen terug**, base64 in de response. Het veld
   heet `label_details` in het schema maar `label` in Sendclouds eigen
   voorbeeld, en `data` is soms een object en soms een lijst — accepteer alle
   vier de vormen.

3. **Meerdere pakketten geven géén label terug**, alleen ids. Daarna
   `GET /v3/shipments/{id}` pollen tot elk pakket een document van type `label`
   met een `link` heeft, en die links los downloaden. De async endpoint faalt
   bovendien "gracefully": `errors` kan naast `data` staan, dus controleer daar
   expliciet op.

4. **DPI is streng.** PDF accepteert alleen 72, PNG 150 of 300, ZPL negeert het.
   Een verkeerde combinatie geeft een 400. De app weigert het al bij het
   opstarten (`app/verzending/config.py`).

5. **Orders worden asynchroon opgeslagen.** Een `201` betekent niet dat de order
   verzendklaar is. Bij zoeken direct na aanmaken: kort blijven herhalen
   (standaard 6 seconden) in plaats van meteen "niet gevonden" melden.

6. **Het ordernummer is genoeg** om een label aan te vragen; er is geen
   id-lookup nodig. Vergelijk ordernummers tolerant: pakbonnen en webshops zijn
   het oneens over de leidende `#`.

7. **Multicollo eist `parcel_items` per pakket.** Wie niet weet wat er in welke
   doos zit, moet de regels willekeurig verdelen — de totalen kloppen dan wel,
   maar de douane-inhoud per doos niet. De PH-app weet het wél (één krat is één
   doos) en koppelt de gescande regels op naam aan de Sendcloud-`item_id`'s;
   lukt dat niet volledig, dan valt hij terug op de willekeurige verdeling en
   **meldt dat zichtbaar**. Neem dat gedrag over: stilzwijgend gokken is erger
   dan zeggen dat je gokt.

8. **Foutmeldingen** komen in drie vormen: `errors` (lijst of object met
   `detail`/`title`), `error.message`, of niets bruikbaars. Val terug op de
   statuscode, maar geef altijd iets wat een magazijnmedewerker kan lezen.

### Wat nog geverifieerd moet worden

De drie `VERIFY:`-markeringen in `app/verzending/sendcloud.py`: het zoeken van
een order, de lijst met verzendopties en het aanmaken van een losse zending.
Van die drie is alleen de verzendopties-call structureel nodig — het zoeken
vervalt grotendeels zodra de app de levering zelf aanmaakt, en losse zendingen
zijn een randgeval.

---

## 3. Printen — labelprinter

Twee backends, allebei **Getest** in de zin dat de code werkt en de paden
gedekt zijn; niet tegen echte hardware in dit project.

### Sendcloud Print Client (standaard)

Draait als lokale applicatie en biedt een kleine HTTP-API op de machine waar
hij staat, standaard `http://127.0.0.1:1903`:

- `GET /printers` — lijst printers, om een id te vinden zonder gokken.
- `POST /printers/{id}/print` — printen. Eerst proberen met `{"path": "..."}`;
  oudere builds (en elke opstelling waar de client op een andere machine draait)
  willen het bestand zelf als multipart upload. Doe beide, in die volgorde.

### CUPS

`lp -h <host> -d <printer> <bestand>` en `lpstat -a` om printers te vinden.
Nodig zodra elke tafel zijn eigen USB-printer heeft: de app draait dan centraal
en stuurt per tafel naar de CUPS-host van die tafel. De tafels staan in een
JSON-bestand (`config/stations.example.json`):

```json
[{"id": "tafel-01", "name": "Tafel 1", "cups_host": "tafel-01.local", "cups_printer": "zd220"}]
```

Een tafel print altijd via zijn eigen CUPS-host, ook als de standaardbackend de
Print Client is.

### Praktijk

- Labels worden eerst naar een spooldirectory geschreven en dan geprint; dat
  bestand blijft staan en maakt opnieuw printen mogelijk zonder een nieuwe
  zending aan te maken.
- Op de Zebra ZD220: begin met **PDF op 72 dpi**. Het A6-label van Sendcloud is
  vector, dus de driver schaalt netjes naar 203 dpi. Alleen als barcodes slecht
  scannen is ZPL de moeite — dat gaat langs de driver heen en vraagt een raw
  printerwachtrij. Labelformaat 102 × 150 mm, printer één keer kalibreren.
- `PRINT_BACKEND=none` schrijft alleen naar de spool. Gebruik dat in tests en
  demo's.

---

## 4. Shopify Admin API — fulfilments

**Status: Aangenomen.** Deze app schrijft géén fulfilments; dat doet SRS. Wat
hier staat is onderzocht via de Admin API over de laatste 50 verzonden orders
en is werk voor wie de fulfilment-kant bouwt.

### Wat er nu misgaat

Van de 30 orders met een trackingnummer had **geen enkele** een vervoerder:

```json
{"company": null, "number": "3SDTBH7046594", "url": null}
```

Shopify maakt alleen een klikbare track & trace-link als de fulfilment de
vervoerder benoemt. Nu krijgt elke klant een kale code: geen knop in de
verzendmail, geen tracking op de bestelstatuspagina. Dit raakt vandaag iedere
klant en staat los van alle andere functionaliteit.

**Geef bij het schrijven van de fulfilment altijd `company` mee** (PostNL, DHL).
De vervoerder is beschikbaar: hij komt uit de verzendoptiecode (`postnl:standard`
→ `postnl`) en wordt per levering bewaard in `controles.zending_vervoerder`,
naast de trackingnummers.

Verder gezien: één order had `specialdelivery` als trackingnummer, met de hand
ingetypt bij eigen transport. Beter een mail "wij bezorgen zelf" dan een code
die nergens heen leidt.

### Deelleveringen

Registreer **per levering een deelfulfilment** met alleen de verzonden regels.
Dan meldt Shopify de klant zelf wat er onderweg is en blijft de rest zichtbaar
als nog te leveren. De order pas afsluiten bij de laatste levering. De PH-app
levert daarvoor per levering: de referentie (`WEB-104903-1`), de getelde regels
met barcode en aantal, de vervoerder en de trackingnummers.

Let op: één fulfilment kan meerdere trackingnummers dragen — handig bij
multicollo, waar elke doos zijn eigen nummer heeft.

**Te verifiëren:** de exacte mutatie en velden hangen af van de API-versie die
in gebruik is. Controleer dat tegen de actuele Admin API voordat je schrijft.

---

## 5. De PH-app zelf — HTTP-API

**Status: Getest**, gedekt door 114 tests. Bruikbaar als je een andere
frontend, een wandmonitor of een integratie wilt bouwen.

Starten: `uvicorn app.main:maak_app --factory`. Alle antwoorden zijn HTML
behalve waar hieronder JSON staat. Fouten komen terug als
`{"fout": "leesbare tekst"}` met status **409** (mag nu niet) of **502**
(SRS/Sendcloud niet bereikbaar).

| Methode en pad | Wat het doet |
|---|---|
| `GET /` | overzicht (HTML) |
| `GET /hal` | plattegrond (HTML) |
| `GET /api/hal` | **JSON**: per vakcode toestand, order, signaal, leeftijd |
| `POST /open` | scan van PH-code, orderreferentie of artikel → redirect naar de juiste PH |
| `GET /ph/{code}` | controlescherm (HTML); start de controle vanzelf bij een complete PH |
| `POST /ph/{code}/start` | controle starten (`naam`, `deellevering=1`) |
| `POST /api/ph/{code}/scan` | **JSON** in `{"barcode": "..."}` → scanresultaat + volledige controlestatus |
| `POST /api/ph/{code}/regel` | **JSON** `{"barcode", "aantal"?, "conditie"?, "notitie"?}` |
| `POST /api/ph/{code}/krat` | **JSON** `{"krat": 2}` — vanaf nu scant alles in die doos |
| `POST /api/ph/{code}/akkoord` | **JSON** → `{pakbon_url, srs_referentie, seconden, volgende_ph, labels, label_fout}` |
| `POST /ph/{code}/akkoord` | zelfde, als formulier, redirect naar de pakbon |
| `POST /ph/{code}/afwijking` | afwijking vastleggen; zet de PH in onderzoek |
| `POST /ph/{code}/onderzoek` en `/onderzoek/afronden` | onderzoek openen en sluiten |
| `POST /ph/{code}/label/opnieuw` | opgeslagen label opnieuw printen (maakt geen nieuwe zending) |
| `POST /ph/{code}/label/alsnog` | label maken na een eerdere storing |
| `GET /ph/{code}/pakbon?direct=1` | pakbon per doos (HTML, printbaar); `direct=1` opent het printvenster |
| `GET /verzending`, `POST /verzending` | zending zonder PH (retour, nazending) |
| `GET /gezondheid` | **JSON** `{app, srs, srs_backend, aantal_phs}`, 503 als SRS eruit ligt |

De scan-response bevat de hele controlestatus (`regels` met `aantal_geteld`,
`per_krat`, `conditie`; `mag_akkoord`; `redenen`; `kratten`; `actieve_krat`),
zodat een client na elke scan alles kan hertekenen zonder extra call.

### Eigen opslag (SQLite)

Wat SRS en Sendcloud niet bijhouden, houdt de app zelf bij: controles met hun
regels en kratverdeling, labelbestanden met trackingnummer per doos,
waarnemingen per PH (sinds wanneer gevuld, wanneer voor het laatst veranderd,
sinds wanneer compleet), onderzoeken, en een audittrail van elke gebeurtenis.
De waarnemingen zijn de basis voor de vastlopersignalering — zonder die tabel
is er geen manier om te weten dat een order stilligt.

---

## 6. Instellingen

Alles via omgevingsvariabelen.

| Groep | Variabelen |
|---|---|
| SRS | `SRS_BACKEND` (mock/rest), `SRS_BASE_URL`, `SRS_AUTH_TYPE`, `SRS_USERNAME`, `SRS_PASSWORD`, `SRS_TOKEN`, `SRS_API_KEY_HEADER`, `SRS_TIMEOUT`, `SRS_VERIFY_SSL`, `SRS_MAPPING_FILE`, `SRS_FIXTURE_FILE` |
| Sendcloud | `SENDCLOUD_PUBLIC_KEY`, `SENDCLOUD_SECRET_KEY`, `SENDCLOUD_INTEGRATION_ID`, `SENDCLOUD_API_BASE`, `SENDCLOUD_TIMEOUT_SECONDS`, `LOOKUP_RETRY_SECONDS`, `LABEL_MIME_TYPE`, `LABEL_DPI`, `APPLY_SHIPPING_RULES`, `CONTRACT_ID`, `SHIPPING_OPTIONS_FILE`, `STANDAARD_VERZENDMETHODE`, `GIFTCARD_PATROON`, `VERZENDING_DEMO` |
| Printen | `PRINT_BACKEND`, `PRINT_CLIENT_URL`, `PRINT_CLIENT_PRINTER_ID`, `CUPS_PRINTER`, `STATIONS_FILE`, `STATION_ID`, `SPOOL_DIR` |
| Signalering | `PH_LETOP_UREN` (4), `PH_VASTLOPER_UREN` (24), `PH_STILSTAND_UREN` (8) |
| Snelheid | `PH_SNELMODUS`, `PH_AUTO_START` |
| Overig | `PH_DB`, `HAL_INDELING`, `BEDRIJF_*` (pakbon) |

Zonder Sendcloud-sleutels slaat de app de labelstap over en werkt de rest
gewoon. `VERZENDING_DEMO=true` draait de hele keten op nepdata, inclusief
printen naar de spool. `SRS_BACKEND=mock` doet hetzelfde voor SRS.

---

## 7. Regels die overeind moeten blijven

Deze volgorde en grenzen zijn met opzet zo gekozen. Wie een nieuwe app bouwt op
dezelfde diensten, loopt zonder deze regels tegen dezelfde problemen aan.

1. **SRS eerst, label daarna.** De afmelding naar SRS gaat vooraf aan het
   aanmaken van het label. Mislukt de afmelding, dan is er geen pakbon en geen
   label — nooit een pakbon zonder afmelding. Mislukt het label, dan blijft de
   afmelding staan en komt de PH in onderzoek: een storing bij Sendcloud mag het
   magazijn niet stilleggen.
2. **Opnieuw printen is niet opnieuw aanmaken.** Bewaar het labelbestand. Een
   tweede aanvraag bij Sendcloud is een tweede zending, en dus een tweede
   betaling en een tweede trackingnummer bij de klant.
3. **Giftcards horen niet in een doos.** Ze worden in Shopify al digitaal
   afgemeld; meesturen betekent dubbel. Een order met alleen giftcards krijgt
   helemaal geen label.
4. **Een deellevering heeft een eigen referentie** (`WEB-104903-1`, `-2`) en
   stuurt alleen de getelde regels door. De klant hoort op de pakbon te lezen
   wat er nog volgt.
5. **Nooit stilzwijgend gokken.** Waar de kratverdeling niet op de Sendcloud-
   regels te leggen is, gebeurt dat zichtbaar in beeld.
6. **Meet zelf wat de bron niet weet.** SRS geeft geen leeftijd van een order in
   een vak, dus houd waarnemingen bij. Zonder dat bestaat "deze order ligt hier
   al twee dagen" niet.

---

## 8. Waar de code staat

```
ph_controle/app/srs/          SRS-adapter: base (protocol), mock, rest + veldmapping
ph_controle/app/verzending/   Sendcloud-client, printen, stations, kratverdeling
ph_controle/app/service.py    het proces: openen, scannen, akkoord, onderzoek, labels
ph_controle/app/store.py      SQLite: controles, labels, waarnemingen, onderzoeken, log
ph_controle/app/main.py       HTTP-routes en JSON-API
ph_controle/config/           srs_rest.json, shipping-options.example.json, stations.example.json
ph_controle/tests/            114 tests, waarvan 17 met gemockte Sendcloud-antwoorden
```

Draaien zonder enige koppeling:

```bash
cd ph_controle
python -m venv .venv && .venv/bin/pip install -r requirements.txt
SRS_BACKEND=mock VERZENDING_DEMO=true \
  .venv/bin/python -m uvicorn app.main:maak_app --factory --port 8000
```
