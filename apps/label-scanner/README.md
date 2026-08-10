# Label scanner

Touch-screen tool for the warehouse: scan the order number on a packing slip,
tap the number of parcels and the shipping method, and the Sendcloud label
comes out of the label printer.

```
   scan barcode  ──►  order + address on screen
                      tap 1..6 parcels
                      tap shipping method   ──►  Sendcloud v3 label  ──►  printer
```

## How it works

1. **Scan.** The barcode scanner behaves as a keyboard and ends with Enter, so
   the scan screen just keeps focus on one input field.
2. **Look up.** The order number is looked up in Sendcloud — the order is
   already there through the Shopify integration, including the address and the
   weight. The shipping method that came in with the order is shown and
   preselected, marked *staat in de order*; any other method can be tapped
   instead.
3. **Announce + label.** Sendcloud API v3 identifies the order by the number on
   the packing slip, so announcing needs no id lookup. One parcel goes through
   `create-label-sync`, which returns the label inline as base64. More parcels
   go through `create-labels-async`, which returns only ids; the app then polls
   `GET /v3/shipments/{id}` and downloads a label per parcel.
4. **Print.** The label is written to the spool directory and handed to the
   Sendcloud Print Client over its local HTTP API.

A label that was created but not printed is **not** an error: the result screen
says so and offers *Opnieuw printen*, because the label already exists in
Sendcloud and re-announcing would create a second one.

## Setting it up

```bash
cd apps/label-scanner
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # fill in the API keys
```

Run it:

```bash
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Then open `http://<pi-address>:8000` on the touch screen.

### Try it without touching Sendcloud

`DEMO_MODE=true` serves a fake order and a fake label for any number you scan.
Use it to set up the screen, test the scanner and train packers:

```bash
DEMO_MODE=true PRINT_BACKEND=none .venv/bin/python -m uvicorn app.main:app --port 8000
```

### Zebra ZD220 over USB

The ZD220 is USB-only, so it has to hang off the machine that runs the Sendcloud
Print Client — the same box this app runs on, in the simplest setup.

Start with the defaults (`LABEL_MIME_TYPE=application/pdf`, `LABEL_DPI=72`).
Sendcloud's A6 label is vector PDF, so the Windows driver scales it to the
printer's 203 dpi without losing barcode sharpness.

Only if labels scan poorly is it worth switching to `application/zpl`. ZPL goes
to the printer untouched and is both sharper and faster, but it bypasses the
driver: it needs a raw print queue (a "Generic / Text Only" queue, or ZDesigner
with pass-through) rather than the normal ZDesigner driver.

Set the label size to 102 x 150 mm in the driver and run the printer's
calibration once, so it finds the gap between labels.

### Finding the printer id

With the Sendcloud Print Client running, `GET /api/printers` (or
`curl http://127.0.0.1:1903/printers` on the machine itself) lists the
printers. Put the id of the label printer in `PRINT_CLIENT_PRINTER_ID`.

If the Print Client runs on the warehouse PC and this app on a Pi, point
`PRINT_CLIENT_URL` at that PC. Printing straight from the Pi to a networked
Zebra/Brother instead? Set `PRINT_BACKEND=cups` and `CUPS_PRINTER`, and set
`LABEL_MIME_TYPE=application/zpl` for a Zebra so the ZPL goes to the printer
untouched.

### Autostart

`deploy/` holds a systemd unit for the app and notes for running the browser in
kiosk mode on the touch screen.

## Choosing the shipping method

API v3 identifies a shipping method by its `shipping_option_code`, a string like
`postnl:standard` or `postnl:letterbox` — not by a numeric id. The app gets the
list of options from the shipping-options endpoint, and falls back to the JSON
file in `SHIPPING_OPTIONS_FILE` when that lookup is unavailable. See
`shipping-options.example.json`. That file is also the natural source for a
printed barcode command sheet, since the barcode can hold the code verbatim.

## Multicollo

Sendcloud requires each box of a multicollo shipment to declare which items it
holds, while the packer only tells us how many boxes there are. The app spreads
the order's item units round-robin over the boxes: an arbitrary but complete
split, which is what the carrier needs for the totals to add up. An order
without item lines, or with fewer items than boxes, is refused with a message
saying so rather than silently shipping something else.

## Still to confirm

Two endpoints are not covered by the documentation used to build this, and are
marked `VERIFY:` in `app/sendcloud.py`:

- **order lookup** (`GET /v3/orders?order_number=…`). Only used to show the
  address before the packer commits — announcing works off the order number
  regardless. Falls back to `GET /v2/parcels?order_number=…` on any 4xx.
- **shipping options** (`POST /v3/shipping-options`). Falls back to
  `SHIPPING_OPTIONS_FILE`.

A third spot is a smaller guess: the parcel document link is documented as
supporting several formats and DPIs without naming the parameters, so the app
tries `?mime_type=&dpi=` and retries without them.

Everything Sendcloud-specific lives in `app/sendcloud.py`; the endpoint
constants are at the top of that file.

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

The Sendcloud calls are mocked with `respx`, so the suite never touches the
live account.
