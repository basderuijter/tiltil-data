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
3. **Announce + label.** Sendcloud API v3 creates the label and returns it
   inline in the response (base64), so there is no second download step.
   One parcel uses `create-label-sync`, multiple parcels use
   `create-labels-async` (multicollo) and the app waits for the job.
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

## Before the first live run

The Sendcloud developer portal was unreachable from the environment this was
built in, so two request shapes are written from the documented v3 behaviour and
are worth confirming against <https://sendcloud.dev> with a single test order:

- the order-lookup endpoint (`GET /v3/orders?order_number=…`) — marked
  `VERIFY:` in `app/sendcloud.py`. If v3 answers 400/404 the app falls back to
  the long-standing `GET /v2/parcels?order_number=…`, so lookups keep working
  either way.
- the poll path for the multicollo job (`/v3/orders/create-labels-async/{id}`).
  The app prefers the `status_url` from Sendcloud's own response when it
  contains one.

Everything Sendcloud-specific lives in `app/sendcloud.py`; the endpoint
constants are at the top of that file.

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

The Sendcloud calls are mocked with `respx`, so the suite never touches the
live account.
