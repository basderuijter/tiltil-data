# Webservices en API's — TILTIL magazijn en webshop

Naslagwerk voor iedereen die een tool bouwt rond bestellen, verzenden en
labelen. Alles hieronder is in de praktijk uitgezocht: waar de docs uitsluitsel
gaven staat dat er letterlijk in, waar iets is afgeleid of live waargenomen
staat dat er ook bij. Dat onderscheid is belangrijk — verkeerd gokken op een
request-vorm kost een middag.

Elke paragraaf sluit af met de valkuilen. Lees die vóór je bouwt; ze zijn stuk
voor stuk een keer misgegaan.

**Betrouwbaarheid van wat hier staat:**

| Markering | Betekenis |
|---|---|
| ✅ **Gedocumenteerd** | Uit de officiële OpenAPI/schema-documentatie |
| 🔍 **Live waargenomen** | Zelf gemeten tegen de productieomgeving |
| ⚠️ **Afgeleid** | Beredeneerd uit aanpalende docs — verifieer vóór productie |

---

## 1. Sendcloud API v3

De aanbevolen versie voor nieuwe integraties. Ondersteunt multicollo, native ZPL
en levert het label direct in de response.

**Basis-URL:** `https://panel.sendcloud.sc/api/v3`

**Authenticatie** ✅ — HTTP Basic met de publieke sleutel als gebruikersnaam en
de geheime sleutel als wachtwoord. Te vinden in het paneel onder Settings →
Integrations → Sendcloud API. OAuth2 client credentials kan ook, via
`https://account.sendcloud.com/oauth2/token/` met scope `api`.

```python
httpx.post(url, auth=(public_key, secret_key), json=body)
```

**Foutformaat** ✅ — JSON:API-stijl. Let op: fouten komen soms mét een geslaagde
statuscode terug, zie de async-endpoint hieronder.

```json
{"errors": [{"status": "400", "code": "validation_error",
             "title": "Invalid order", "detail": "The order has no valid shipping address.",
             "source": {"pointer": "/data/attributes/orders/[1]"}}]}
```

### 1.1 Eén label maken — `POST /orders/create-label-sync` ✅

De eenvoudigste weg: het label komt als base64 mee in dezelfde response.

```json
{
  "integration_id": 70,
  "label_details": { "mime_type": "application/pdf", "dpi": 72 },
  "order": { "order_number": "ORDER-25763", "apply_shipping_rules": false },
  "ship_with": {
    "type": "shipping_option_code",
    "properties": { "shipping_option_code": "postnl:standard", "contract_id": 517 }
  }
}
```

Antwoord `201` met `data`: `parcel_id`, `shipment_id`, `tracking_number`,
`tracking_url`, het labelbestand, en `documents[]` met downloadlinks.

> **Valkuil 1.** De officiële documentatie is intern tegenstrijdig over het
> labelveld: het schema noemt het `label_details`, het eigen voorbeeld `label`.
> Accepteer allebei bij het uitlezen.
>
> **Valkuil 2.** `data` is in het schema een object, in het voorbeeld een array.
> Behandel beide vormen.
>
> **Valkuil 3.** DPI is streng gevalideerd: PDF accepteert **alleen 72**, PNG
> 150 of 300. ZPL negeert de instelling — daar bepaalt de vervoerder de
> resolutie, meestal 203. Een verkeerde combinatie geeft een 400. Valideer dit
> bij het opstarten van je app, niet bij het eerste label.

### 1.2 Meerdere pakketten — `POST /orders/create-labels-async` ✅

Voor multicollo én voor meerdere orders tegelijk (maximaal 20).

```json
{
  "integration_id": 70,
  "orders": [{
    "order_number": "ORDER-25763",
    "apply_shipping_rules": false,
    "parcels": [
      { "parcel_items": [{ "item_id": "5552", "quantity": 1 }],
        "weight": { "value": "1.320", "unit": "kg" } },
      { "parcel_items": [{ "item_id": "763", "quantity": 12 }] }
    ]
  }]
}
```

> **Valkuil 4 — de grootste.** Deze endpoint geeft **geen labelbestand terug**.
> Alleen `parcels_ids` en `shipment_id`. Het label haal je daarna op via
> `GET /shipments/{id}` → `parcels[].documents[].link`. Wie hier een label
> verwacht, wacht op iets dat nooit komt.
>
> **Valkuil 5.** Elk pakket moet met `parcel_items` opgeven *wat erin zit*.
> Wie alleen "drie dozen" weet, kan dit niet invullen. Zorg dat het proces die
> informatie oplevert — bijvoorbeeld door bij het inpakken per doos te scannen.
> Zonder echte verdeling kloppen gewicht en douane-inhoud per doos niet.
>
> **Valkuil 6.** De endpoint faalt gedeeltelijk: bij een `202` kunnen `data` en
> `errors` naast elkaar staan. Controleer altijd `errors`, ook bij succes.
> `source.pointer` wijst aan welke order het betreft.

### 1.3 Zending ophalen — `GET /shipments/{id}` ✅

Levert `from_address`, `to_address`, `ship_with`, `errors[]` en per pakket:
`status.code`, `tracking_number`, `tracking_numbers[]`, `parcel_items[]` en
`documents[]` met een `link` per document (label, douanepapieren).

De documentlink ondersteunt volgens de docs meerdere formaten en DPI's, maar de
parameternamen worden niet genoemd. ⚠️ Wij sturen `?mime_type=&dpi=` en vallen
terug op een kale GET als dat wordt geweigerd.

### 1.4 Order ophalen — `GET /orders/{id}` ✅

Op Sendclouds eigen numerieke id, niet op ordernummer. Geeft
`order_details.order_items[]` met `item_id`, `quantity`, `name`, `sku` —
precies wat je nodig hebt voor de `parcel_items` van een multicollo-zending.
Verder `shipping_address`, `shipping_details.ship_with` en
`shipping_details.delivery_indicator` (de vrije tekst uit de checkout).

### 1.5 Contracten — `GET /contracts` ✅

Voor het opzoeken van een `contract_id`. Filters: `carrier_code`, `is_active`,
`country_code`. Cursorpaginering via de `Link`-header (RFC8288), niet via een
veld in de body.

Meestal heb je dit niet nodig: bij precies één standaardcontract per vervoerder
(`is_default_per_carrier: true`) mag `contract_id` leeg blijven en kiest
Sendcloud zelf.

### 1.6 Niet-geverifieerde endpoints ⚠️

Deze zijn nodig maar de documentatie ontbrak:

| Endpoint | Waarvoor | Status |
|---|---|---|
| `POST /shipping-options` | De lijst met `shipping_option_code`s | Vorm onbekend. Wij vallen terug op een JSON-bestand met eigen codes |
| `GET /orders?order_number=` | Order opzoeken op nummer | Wij vallen terug op v2 `GET /parcels?order_number=` |
| `POST /shipments` | Losse zending zonder order | Gemodelleerd op het schema uit *Retrieve a shipment* |

### 1.7 Verzendmethodes: codes, geen nummers ✅

Het grootste verschil met v2. Een methode heet `postnl:standard`,
`postnl:letterbox` of `dhl_express:worldwide/incoterm=dap` — een tekstcode, geen
integer. Handig neveneffect: zo'n code past letterlijk in een barcode, dus je
kunt een gelamineerde kaart met commando-barcodes maken waarmee een magazijn­
medewerker de verzendmethode scant in plaats van aantikt.

### 1.8 Asynchroon opslaan ✅

**Een `201` van de Orders API betekent niet dat de order verzendklaar is.** De
docs waarschuwen hier expliciet voor. Maak je een order aan en vraag je vlak
daarna een label, dan kan die order nog niet bestaan. Controleer eerst met
*Retrieve an order*, of bouw een korte retry in — enkele seconden volstaat.

---

## 2. Sendcloud API v2 (nog bruikbaar)

Niet uitgefaseerd en soms handiger. ⚠️ Onderstaande vormen komen uit ervaring,
niet uit meegeleverde documentatie.

- `GET /api/v2/parcels?order_number=X` → `{"parcels": [...]}` met `name`,
  `address`, `postal_code`, `city`, `country.iso_2`, `shipment.name`,
  `tracking_number`, `status.message`.
- `GET /api/v2/shipping_methods?to_country=NL` → methodes met **numerieke** id's,
  `min_weight`/`max_weight` als strings in kilo's. Deze id's werken **niet** in
  v3.
- `GET /api/v2/brands` — merken; nog geen v3-equivalent, ook volgens de v3-docs.

---

## 3. Sendcloud Print Client (lokale HTTP-API)

Een app op de werkplek die een printerwachtrij aanbiedt op `127.0.0.1:1903`.

- `GET /printers` → beschikbare printers met hun id
- `POST /printers/{id}/print` → printopdracht, met het pad naar het PDF-bestand

⚠️ Deze vorm komt uit een supportartikel, niet uit een API-referentie. Reken op
variatie tussen versies; wij proberen eerst `{"path": "..."}` en daarna een
multipart-upload.

> **Belangrijkste beperking:** de Print Client draait **alleen op Windows en
> macOS**, niet op Linux of ARM. Een Raspberry Pi kan hem dus niet draaien. Wil
> je Linux gebruiken, print dan rechtstreeks via CUPS.

---

## 4. Rechtstreeks printen via CUPS

Het alternatief zonder Print Client, en op schaal het eenvoudigst: elke werkplek
draait CUPS met zijn eigen USB-labelprinter, en de centrale app stuurt het label
naar de juiste host.

```bash
lp -h werkplek-03.local -d zd220 /pad/naar/label.pdf
lpstat -a                                   # welke printers zijn er
```

**Zebra ZD220 in de praktijk** 🔍 — USB-only, dus hij moet aan de machine hangen
die de printopdracht ontvangt. Begin met PDF op 72 dpi: het A6-label van
Sendcloud is vector, dus de driver schaalt netjes naar de 203 dpi van de
printer. Alleen als barcodes slecht scannen is ZPL de moeite waard; dat gaat
langs de driver heen en vraagt een raw printerwachtrij ("Generic / Text Only" of
ZDesigner met pass-through). Zet het labelformaat op 102 × 150 mm en kalibreer de
printer één keer, anders vindt hij de tussenruimte tussen labels niet.

---

## 5. Shopify Admin GraphQL API

**Endpoint:** `https://{shop}.myshopify.com/admin/api/{versie}/graphql.json`,
met `X-Shopify-Access-Token`.

### 5.1 Fulfilment aanmaken met werkende tracking ✅

Gevalideerd tegen het schema. Dit is de mutatie waarmee je de klant informeert:

```graphql
mutation CreatePartialFulfilment($fulfillment: FulfillmentInput!) {
  fulfillmentCreate(fulfillment: $fulfillment) {
    fulfillment { id status trackingInfo { company number url } }
    userErrors { field message }
  }
}
```

```json
{
  "fulfillment": {
    "lineItemsByFulfillmentOrder": [{
      "fulfillmentOrderId": "gid://shopify/FulfillmentOrder/123",
      "fulfillmentOrderLineItems": [{ "id": "gid://shopify/FulfillmentOrderLineItem/456", "quantity": 1 }]
    }],
    "trackingInfo": { "company": "PostNL", "number": "3SABC123" },
    "notifyCustomer": true
  }
}
```

> **Valkuil 7 — dit gaat vandaag in productie mis.** 🔍 Van de laatste 50
> verzonden orders hebben er 30 een trackingnummer, en bij **alle dertig** is
> `company` leeg:
>
> ```json
> {"company": null, "number": "3SDTBH7046594", "url": null}
> ```
>
> Shopify bouwt de volglink alleen automatisch als je de vervoerder benoemt.
> Zonder `company` én zonder `url` krijgt de klant een kale code: geen knop in de
> verzendmail, geen tracking op de bestelstatuspagina. Volgens de schema-docs
> raadt Shopify aan om **allebei** mee te sturen, en de naam exact te schrijven
> zoals in hun lijst van bekende vervoerders — hoofdletters tellen mee.

Regels weglaten uit `fulfillmentOrderLineItems` betekent: alle regels van die
fulfillment order. Voor een **deellevering** geef je juist alleen de verzonden
regels op. Shopify meldt de klant dan uit zichzelf wát er onderweg is en houdt
de rest zichtbaar als nog te leveren. Sluit de order pas af bij de laatste
levering.

`trackingInfo` kent ook `numbers[]` en `urls[]`, zodat één fulfilment meerdere
trackingnummers kan dragen — nodig als een levering in twee dozen gaat.

### 5.2 Open regels ophalen ✅

Gevalideerd:

```graphql
query OpenFulfilmentOrders($id: ID!) {
  order(id: $id) {
    name
    fulfillmentOrders(first: 10, query: "status:open") {
      edges { node {
        id status
        lineItems(first: 50) {
          edges { node { id remainingQuantity lineItem { name sku } } }
        }
      } }
    }
  }
}
```

### 5.3 Wat de huidige winkel doet 🔍

Gemeten over de laatste 50 verzonden orders:

- Fulfilments zijn van het type **Manual** op locatie "Online" — ze worden via
  de API weggeschreven, niet via een fulfillment service.
- Ze komen in golven binnen, ongeveer één tot twee uur na de bestelling.
- Verzendmethodes in de checkout zijn **custom tarieven** met de titel "PostNL"
  en zonder `carrierIdentifier`. Die vrije tekst komt in Sendcloud terecht als
  `delivery_indicator`.
- Twee trackingnummerpatronen: `3SDTBH…` (PostNL) en `1704147…` (tweede
  vervoerder).
- Eén order had `specialdelivery` als trackingnummer, met de hand ingetypt bij
  eigen transport. Beter een mail "wij bezorgen zelf" dan een code die nergens
  heen leidt.
- **Deelleveringen komen niet voor.** Twee orders staan op
  `PARTIALLY_FULFILLED`, maar in beide gevallen is het afgehandelde deel een
  digitaal automatisch afgemelde e-giftcard terwijl het fysieke artikel
  openstaat. Uit het magazijn gaat nooit een halve order weg.

> **Valkuil 8.** Giftcards worden door Shopify zelf afgemeld. Neem ze niet mee
> in een magazijnlevering, anders meld je ze een tweede keer af.

### 5.4 Zoeken en paginering

`orders(query: "fulfillment_status:shipped OR fulfillment_status:partial")`
gebruikt de zoeksyntaxis van de admin. Sorteren doe je met de aparte argumenten
`sortKey` en `reverse`, **niet** met `sort:` in de querystring. Pagineren met
`pageInfo { hasNextPage endCursor }` en het cursorargument `after`.

---

## 6. SRS

Het voorraadsysteem achter de PH-controle. De koppeling zit in
`ph_controle/app/srs/` met een `rest`- en een `mock`-implementatie achter één
interface, geconfigureerd via `config/srs_rest.json`. Lees die code; hier is
geen aparte documentatie van.

Wat er functioneel toe doet: SRS weet wat er in een pigeonhole ligt, maar houdt
**geen historie** bij van hoe lang iets er ligt. De PH-app meet dat daarom zelf
om vastlopers te signaleren.

---

## 7. Aanbevolen ketenopzet

De rolverdeling die uit dit alles volgt:

```
Shopify        de bestelling van de klant
PH-controle    controleert, bepaalt de levering, maakt de Sendcloud-order,
               maakt en print label(s) + pakbon(nen)
Sendcloud      label en trackingnummer
Shopify        deelfulfilment per levering, mét vervoerder → klant krijgt
               een werkende volglink
```

Drie principes die uit de valkuilen volgen:

**Een mislukte printopdracht is geen mislukt label.** Het pakket bestaat op dat
moment al bij Sendcloud. Opnieuw aanmelden levert een tweede label en dubbele
verzendkosten op. Bied opnieuw printen aan, geen opnieuw aanmaken.

**Een mislukte terugkoppeling mag het magazijn niet blokkeren.** Maar log hem
luid: een verloren trackingnummer is een klant die niets hoort.

**Bepaal het aantal dozen vóór het aanmelden.** Sendcloud legt dat aantal vast
op het moment van aanmelden; er kan achteraf geen pakket bij. Een extra doos
wordt dan een aparte zending met een eigen trackingnummer.
