# Overdracht: labels verhuizen naar de PH-app

Deze app verdwijnt als losse tool. Alles wat met Sendcloud en labels te maken
heeft gaat naar `ph_controle/`. Dit document is de opdracht voor die verhuizing.

Bronbranch: `claude/sendcloud-label-scanner-tool-4wiicx`, map `apps/label-scanner/`.

## Waarom

De PH-medewerker legt de producten in kratten. Eén krat is één doos, dus op dat
moment is bekend hoeveel labels er nodig zijn — precies de informatie die aan de
inpaktafel ontbrak. Pakbon en label gaan mee in de krat, dus ze kunnen niet van
de goederen gescheiden raken.

Daarmee vervalt de reden voor een aparte scan-app aan de inpaktafel. De
inpakkers halen alleen nog pakbon(nen) en label(s) uit de krat en pakken in.

Wat er tegelijk aan storingen verdwijnt: geen barcode op de pakbon nodig, geen
opzoekactie in Sendcloud (en dus niet de asynchrone race daaromheen), geen
"order niet gevonden", geen dubbele scan die een tweede label kost, en geen
HTTP-callback tussen twee apps die kan mislukken.

**De inpaktafels hebben hierna geen pc, scanner of scherm meer nodig.** Alleen
de PH-tafels: één machine met een A4-printer voor de pakbon en een Zebra ZD220
voor de labels.

## Wat verhuist

Deze bestanden zijn UI-vrij en kunnen als pakket mee, bijvoorbeeld naar
`ph_controle/app/verzending/`:

| Bestand | Wat het doet |
|---|---|
| `app/sendcloud.py` | Sendcloud v3 client: order zoeken, verzendopties, labels maken |
| `app/printing.py` | Printen via Sendcloud Print Client of CUPS, met spooldirectory |
| `app/stations.py` | Welke tafel print op welke CUPS-host |
| `app/models.py` | `Order`, `OrderItem`, `ShippingOption`, `Label`, `LabelResult` |
| `app/demo.py` | Nepdata om zonder Sendcloud te kunnen draaien |
| `tests/test_sendcloud.py` | 20+ tests, allemaal met `respx` gemockt |

Uit `app/config.py` de Sendcloud- en printinstellingen overnemen; de rest van
dat bestand hoort bij de webapp.

**Vervalt:** `app/main.py`, `app/static/*`, `app/callback.py`, `tests/test_api.py`,
`deploy/*`. De callback was er alleen om twee apps te koppelen — binnen één app
roep je de functie direct aan.

## Wat er in de PH-app bij moet

**1. `mag_uit_ph` openzetten voor deelleveringen.** Nu staat in
`completeness.py`:

```python
@property
def mag_uit_ph(self) -> bool:
    """Alleen een volledig gevulde PH mag leeggehaald worden."""
    return self.status == COMPLEET
```

Zolang dit zo blijft, bestaan deelleveringen niet. Een incomplete PH moet
vrijgegeven kunnen worden als deellevering met een eigen referentie
(`1042-1`, `1042-2`).

**2. Aantal kratten vastleggen bij de controle.** Eén krat = één doos = één
label. Dit is het moment waarop dat bekend is.

**3. Bij akkoord: levering aanmaken in Sendcloud, label maken, printen.**
Alleen de getelde regels, met het adres en de verzendmethode. Geen giftcards —
die worden in Shopify al digitaal afgemeld en zouden anders dubbel gaan.

**4. Artikelen per krat vastleggen.** De app scant elk artikel toch al. Leg bij
meerdere kratten vast in welke krat het gaat. Sendcloud eist voor multicollo per
pakket een `parcel_items`-lijst; nu verdeelt `split_items()` in `sendcloud.py`
de regels willekeurig omdat niemand het beter weet. Met de kratinformatie wordt
dat een echte verdeling, en kloppen gewicht en douane-inhoud per doos.

**5. Pakbon per krat**, met "doos 1 van 2" en de inhoud van díe doos. De klant
die doos 1 opent ziet dan meteen dat er nog een doos komt. Eén pakbon in één van
twee dozen laat de ontvanger van de andere doos in het ongewisse.

**6. Opnieuw printen.** Een thermische printer verfrommelt labels; dat gebeurt
vaker dan alle andere storingen samen. Zonder knop staat de tafel stil.

**7. Zendingen zonder PH** — retouren, nazendingen, een klantenservicepakket.
Klein scherm, adres invullen of order opzoeken, label eruit.

**8. Label-fouten mogen de SRS-afmelding niet blokkeren.** De controle is
akkoord; als Sendcloud plat ligt moet het proces doorlopen en het label later
gemaakt kunnen worden.

## Sendcloud v3: wat we al weten

Dit is uitgezocht en getest — hier hoef je niet opnieuw achter te komen.

- **Verzendmethode is een tekstcode, geen nummer.** `ship_with` met
  `{"type": "shipping_option_code", "properties": {"shipping_option_code":
  "postnl:standard", "contract_id": null}}`. `contract_id` mag leeg: Sendcloud
  pakt dan het standaardcontract per vervoerder.
- **Eén pakket** → `POST /v3/orders/create-label-sync`. Het label komt als
  base64 mee in de response. Het veld heet `label_details` in het schema maar
  `label` in Sendclouds eigen voorbeeld; de client accepteert allebei.
- **Meerdere pakketten** → `POST /v3/orders/create-labels-async`. Die geeft
  **geen label** terug, alleen ids. Daarna `GET /v3/shipments/{id}` pollen en per
  pakket het label downloaden via `documents[].link`.
- **DPI is streng.** PDF accepteert alleen 72, PNG 150 of 300. ZPL negeert het.
  Een verkeerde combinatie geeft een 400; `config.py` weigert het bij opstarten.
- **Orders worden asynchroon opgeslagen.** Een `201` betekent niet dat de order
  verzendklaar is. Binnen één app speelt dit minder, maar hou er rekening mee als
  je direct na aanmaken een label vraagt.
- **Ordernummer is genoeg** om een label aan te vragen; geen id-lookup nodig.

## Shopify: een fout die er nu al is

Onderzocht via de Admin API over de laatste 50 verzonden orders. Van de 30 met
een trackingnummer heeft **geen enkele** een vervoerder:

```json
{"company": null, "number": "3SDTBH7046594", "url": null}
```

Shopify maakt alleen een klikbare track & trace-link als de fulfilment de
vervoerder benoemt. Nu krijgt elke klant een kale code zonder link — geen knop in
de verzendmail, geen tracking op de bestelstatuspagina. Dit staat los van
deelleveringen en raakt vandaag iedere klant.

Bij het schrijven van de fulfilment dus **altijd `company` meegeven** (PostNL,
DHL). De vervoerder zit in `ShippingOption.carrier`.

Verder gezien: order #456850 heeft `specialdelivery` als trackingnummer — met de
hand ingetypt bij eigen transport. Beter een mail "wij bezorgen zelf" dan een
code die nergens heen leidt.

En: **registreer per levering een deelfulfilment** met alleen de verzonden
regels. Dan meldt Shopify de klant zelf wat er onderweg is en houdt het de rest
zichtbaar als nog te leveren. De order pas afsluiten bij de laatste levering.

## Nog te verifiëren

Drie plekken staan gemarkeerd met `VERIFY:` in `sendcloud.py`, omdat de docs
daarvan niet beschikbaar waren:

1. `GET /v3/orders?order_number=` — het opzoeken van een order. Valt terug op
   `GET /v2/parcels?order_number=`. Na de verhuizing waarschijnlijk overbodig:
   de PH-app maakt de levering zelf en kent het id al.
2. `POST /v3/shipping-options` — de lijst met verzendopties. Valt terug op een
   JSON-bestand (`shipping-options.example.json`). Deze doc is nog steeds nodig.
3. `POST /v3/shipments` — losse zending, gebruikt voor het extra label. Gemodelleerd
   op het schema uit *Retrieve a shipment*. Na de verhuizing minder belangrijk,
   want twee kratten worden gewoon één multicollo-zending.

## Instellingen

Zie `.env.example` voor de volledige lijst met uitleg. Wat mee moet:
`SENDCLOUD_PUBLIC_KEY`, `SENDCLOUD_SECRET_KEY`, `SENDCLOUD_INTEGRATION_ID`,
`LABEL_MIME_TYPE`, `LABEL_DPI`, `APPLY_SHIPPING_RULES`, `CONTRACT_ID`,
`SHIPPING_OPTIONS_FILE`, `PRINT_BACKEND`, `PRINT_CLIENT_URL`,
`PRINT_CLIENT_PRINTER_ID`, `CUPS_PRINTER`, `STATIONS_FILE`, `SPOOL_DIR`.

Op de ZD220: begin met PDF op 72 dpi. Sendclouds A6-label is vector, dus de
driver schaalt netjes naar 203 dpi. Alleen als barcodes slecht scannen is ZPL de
moeite — dat gaat langs de driver heen en vraagt een raw printerwachtrij. Zet het
labelformaat op 102 × 150 mm en kalibreer de printer één keer.
