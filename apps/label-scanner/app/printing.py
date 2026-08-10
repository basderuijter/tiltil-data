"""Sending finished labels to a physical printer.

Two backends are supported:

* ``sendcloud_client`` — the Sendcloud Print Client, which exposes a small HTTP
  API on the machine it runs on (``/printers`` to list, ``/printers/{id}/print``
  to print). This is the default and needs no printer drivers of our own.
* ``cups`` — hand the file to ``lp`` on the local machine. Useful on a Pi that
  drives a networked Zebra/Brother directly.

``none`` writes the label to the spool directory and stops there, which is what
demo mode and the test suite use.
"""

from __future__ import annotations

import asyncio
import subprocess
import uuid
from pathlib import Path

import httpx

from .config import Settings
from .models import Label
from .sendcloud import decode_label

_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/zpl": ".zpl",
    "text/zpl": ".zpl",
    "image/png": ".png",
}


class PrintError(Exception):
    pass


class Printer:
    """Writes labels to the spool directory and delegates the actual printing."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client

    async def print_labels(self, labels: list[Label]) -> None:
        for label in labels:
            path = self._spool(label)
            await self._send(path)

    def _spool(self, label: Label) -> Path:
        directory = self._settings.spool_dir
        directory.mkdir(parents=True, exist_ok=True)
        extension = _EXTENSIONS.get(label.mime_type, ".pdf")
        name = f"{label.parcel_id or uuid.uuid4().hex}{extension}"
        path = directory / name
        path.write_bytes(decode_label(label))
        return path

    async def _send(self, path: Path) -> None:
        backend = self._settings.print_backend
        if backend == "none":
            return
        if backend == "cups":
            await self._print_via_cups(path)
            return
        await self._print_via_sendcloud_client(path)

    # -- backends -------------------------------------------------------------

    async def _print_via_sendcloud_client(self, path: Path) -> None:
        printer_id = self._settings.print_client_printer_id
        if not printer_id:
            raise PrintError(
                "Geen printer ingesteld. Zet PRINT_CLIENT_PRINTER_ID in .env "
                "(zie /api/printers voor de beschikbare printers)."
            )
        url = f"{self._settings.print_client_url.rstrip('/')}/printers/{printer_id}/print"
        try:
            response = await self._client.post(url, json={"path": str(path)}, timeout=20.0)
            if response.status_code >= 400:
                # Older Print Client builds want the file itself rather than a
                # path, which is also the only thing that works when the client
                # runs on a different machine than this app.
                with path.open("rb") as handle:
                    response = await self._client.post(
                        url, files={"file": (path.name, handle, "application/pdf")}, timeout=20.0
                    )
        except httpx.RequestError as exc:
            raise PrintError(
                f"Print Client niet bereikbaar op {self._settings.print_client_url}: {exc}"
            ) from exc

        if response.status_code >= 400:
            raise PrintError(
                f"Print Client weigerde de opdracht (status {response.status_code})."
            )

    async def _print_via_cups(self, path: Path) -> None:
        printer = self._settings.cups_printer
        if not printer:
            raise PrintError("Geen CUPS_PRINTER ingesteld in .env.")
        command = ["lp", "-d", printer, str(path)]
        result = await asyncio.to_thread(
            subprocess.run, command, capture_output=True, text=True
        )
        if result.returncode != 0:
            raise PrintError(f"lp gaf een fout: {result.stderr.strip() or result.returncode}")

    # -- discovery ------------------------------------------------------------

    async def available_printers(self) -> list[dict[str, str]]:
        """List printers, so the installer can pick an id without guessing."""
        if self._settings.print_backend == "cups":
            result = await asyncio.to_thread(
                subprocess.run, ["lpstat", "-a"], capture_output=True, text=True
            )
            return [
                {"id": line.split()[0], "name": line.split()[0]}
                for line in result.stdout.splitlines()
                if line.strip()
            ]

        url = f"{self._settings.print_client_url.rstrip('/')}/printers"
        try:
            response = await self._client.get(url, timeout=10.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise PrintError(f"Print Client niet bereikbaar: {exc}") from exc

        payload = response.json()
        printers = payload if isinstance(payload, list) else payload.get("printers", [])
        return [
            {"id": str(p.get("id", "")), "name": p.get("name") or p.get("id", "")}
            for p in printers
        ]
