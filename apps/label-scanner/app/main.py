"""FastAPI app behind the warehouse touch screen.

Three calls make up the whole flow:

    GET  /api/orders/{order_number}   scan a packing slip
    POST /api/labels                  announce + print
    GET  /api/printers                one-off, to find a printer id

The UI is a single static page served from /.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import demo
from .config import Settings, get_settings
from .models import LabelResult, Order, ShippingMethod
from .printing import PrintError, Printer
from .sendcloud import OrderNotFound, SendcloudClient, SendcloudError

logger = logging.getLogger("label_scanner")

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient() as client:
        app.state.http = client
        yield


app = FastAPI(title="TILTIL label scanner", lifespan=lifespan)


def get_client(settings: Settings = Depends(get_settings)) -> SendcloudClient:
    return SendcloudClient(settings, app.state.http)


def get_printer(settings: Settings = Depends(get_settings)) -> Printer:
    return Printer(settings, app.state.http)


class OrderResponse(BaseModel):
    order: Order
    shipping_methods: list[ShippingMethod]
    max_parcels: int
    demo_mode: bool


class LabelRequest(BaseModel):
    order_number: str
    shipping_method_id: int
    quantity: int = Field(ge=1, le=20)


@app.get("/api/orders/{order_number}", response_model=OrderResponse)
async def read_order(
    order_number: str,
    settings: Settings = Depends(get_settings),
    client: SendcloudClient = Depends(get_client),
) -> OrderResponse:
    if settings.demo_mode:
        order = demo.demo_order(order_number)
        methods = demo.demo_shipping_methods(order)
    else:
        _require_credentials(settings)
        try:
            order = await client.find_order(order_number)
            methods = await client.shipping_methods(order)
        except OrderNotFound as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        except SendcloudError as exc:
            raise HTTPException(status_code=502, detail=exc.message) from exc

    return OrderResponse(
        order=order,
        shipping_methods=methods,
        max_parcels=settings.max_parcels,
        demo_mode=settings.demo_mode,
    )


@app.post("/api/labels", response_model=LabelResult)
async def create_labels(
    request: LabelRequest,
    settings: Settings = Depends(get_settings),
    client: SendcloudClient = Depends(get_client),
    printer: Printer = Depends(get_printer),
) -> LabelResult:
    if settings.demo_mode:
        order = demo.demo_order(request.order_number)
        methods = demo.demo_shipping_methods(order)
        labels = demo.demo_labels(order, request.quantity)
    else:
        _require_credentials(settings)
        try:
            order = await client.find_order(request.order_number)
            methods = await client.shipping_methods(order)
            labels = await client.create_labels(
                order, request.shipping_method_id, request.quantity
            )
        except OrderNotFound as exc:
            raise HTTPException(status_code=404, detail=exc.message) from exc
        except SendcloudError as exc:
            raise HTTPException(status_code=502, detail=exc.message) from exc

    if not labels:
        raise HTTPException(status_code=502, detail="Sendcloud gaf geen label terug.")

    result = LabelResult(
        order_number=order.order_number or request.order_number,
        labels=labels,
        shipping_method_name=_method_name(methods, request.shipping_method_id),
    )

    # The label exists in Sendcloud at this point. A printer that is offline
    # must not read as "label mislukt", so report it separately and let the
    # packer reprint from the result screen.
    try:
        await printer.print_labels(labels)
        result.printed = True
    except PrintError as exc:
        logger.warning("Printen mislukt voor order %s: %s", result.order_number, exc)
        result.print_error = str(exc)

    return result


@app.post("/api/reprint")
async def reprint(
    result: LabelResult, printer: Printer = Depends(get_printer)
) -> dict[str, bool]:
    try:
        await printer.print_labels(result.labels)
    except PrintError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"printed": True}


@app.get("/api/printers")
async def list_printers(printer: Printer = Depends(get_printer)) -> list[dict[str, str]]:
    try:
        return await printer.available_printers()
    except PrintError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/health")
async def health(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    return {
        "ok": settings.demo_mode or settings.has_credentials,
        "demo_mode": settings.demo_mode,
        "print_backend": settings.print_backend,
        "label_mime_type": settings.label_mime_type,
    }


def _require_credentials(settings: Settings) -> None:
    if not settings.has_credentials:
        raise HTTPException(
            status_code=503,
            detail="Sendcloud API-sleutels ontbreken. Vul .env aan en herstart.",
        )


def _method_name(methods: list[ShippingMethod], method_id: int) -> str:
    for method in methods:
        if method.id == method_id:
            return method.name
    return str(method_id)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
