"""
cero_colorize.py — Pinta filas de la hoja de Cero por CodigoProveedor.

Modos:
    highlight   pinta filas:
                  - VERDE  los CodigoProveedor que aparecen en cero_highlight_codes.json["verde"]
                  - NARANJA los que aparecen en cero_highlight_codes.json["naranja"]
    reset       quita color de fondo (vuelve a blanco) de TODAS las filas de datos.

Uso:
    python cero_colorize.py highlight
    python cero_colorize.py reset

Env vars: GOOGLE_SERVICE_ACCOUNT_JSON, CERO_SHEET_ID, CERO_SHEET_TAB.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Iterable

import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("colorize")

VERDE   = {"red": 0.71, "green": 0.93, "blue": 0.78}  # verde claro Material
NARANJA = {"red": 1.00, "green": 0.85, "blue": 0.62}  # naranja claro Material
BLANCO  = {"red": 1.00, "green": 1.00, "blue": 1.00}

COL_CLAVE = "CodigoProveedor"


def _conectar() -> "gspread.Worksheet":
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = os.environ.get("CERO_SHEET_ID")
    tab = os.environ.get("CERO_SHEET_TAB")
    if not (sa_json and sheet_id and tab):
        sys.exit("ERROR: faltan GOOGLE_SERVICE_ACCOUNT_JSON / CERO_SHEET_ID / CERO_SHEET_TAB")
    creds = Credentials.from_service_account_info(
        json.loads(sa_json),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(tab)
    log.info("Conectado a '%s' / '%s'", sh.title, ws.title)
    return ws


def _col_letter(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _ranges_for_codes(ws, codes: Iterable[str], last_col_letter: str) -> list[str]:
    """Devuelve lista de rangos A2:AHN para cada fila cuyo CodigoProveedor matchea."""
    todas = ws.get_all_values()
    if not todas:
        return []
    headers = todas[0]
    if COL_CLAVE not in headers:
        sys.exit(f"ERROR: la hoja no tiene columna {COL_CLAVE}")
    idx_clave = headers.index(COL_CLAVE) + 1  # 1-based
    codes_norm = {c.strip().upper() for c in codes if c}
    out: list[str] = []
    for i, fila in enumerate(todas[1:], start=2):
        if len(fila) < idx_clave:
            continue
        c = (fila[idx_clave - 1] or "").strip().upper()
        if c in codes_norm:
            out.append(f"A{i}:{last_col_letter}{i}")
    return out


def highlight() -> None:
    ws = _conectar()
    headers = ws.row_values(1)
    last_col = _col_letter(len(headers))
    log.info("Hoja con %d columnas (ultima: %s)", len(headers), last_col)

    with open("cero_highlight_codes.json", encoding="utf-8") as f:
        cfg = json.load(f)
    verde_codes   = cfg.get("verde",   [])
    naranja_codes = cfg.get("naranja", [])
    log.info("Pintar VERDE: %d codigos | NARANJA: %d codigos", len(verde_codes), len(naranja_codes))

    rangos_v = _ranges_for_codes(ws, verde_codes, last_col)
    rangos_n = _ranges_for_codes(ws, naranja_codes, last_col)
    log.info("Filas encontradas - VERDE: %d, NARANJA: %d", len(rangos_v), len(rangos_n))

    formats = []
    for rg in rangos_v:
        formats.append({"range": rg, "format": {"backgroundColor": VERDE}})
    for rg in rangos_n:
        formats.append({"range": rg, "format": {"backgroundColor": NARANJA}})

    if not formats:
        log.warning("No hay nada que pintar.")
        return
    # gspread acepta lista grande; si fallara por tamanio, dividir en chunks
    ws.batch_format(formats)
    log.info("Aplicados %d formatos de color.", len(formats))


def reset() -> None:
    ws = _conectar()
    headers = ws.row_values(1)
    last_col = _col_letter(len(headers))
    # Cuantas filas tiene la hoja realmente
    last_row = len(ws.get_all_values())
    if last_row < 2:
        log.warning("Hoja sin datos.")
        return
    rg = f"A2:{last_col}{last_row}"
    log.info("Reseteando color de %s a blanco", rg)
    ws.format(rg, {"backgroundColor": BLANCO})
    log.info("Listo, todas las filas en blanco.")


def debug() -> None:
    ws = _conectar()
    log.info("row_count=%d  col_count=%d", ws.row_count, ws.col_count)
    todas = ws.get_all_values()
    log.info("len(get_all_values)=%d", len(todas))
    headers = todas[0] if todas else []
    # Mostrar las ultimas 5 filas en detalle (primeras 4 columnas)
    log.info("==Ultimas 5 filas de datos (cols A-D):")
    for i, fila in enumerate(todas[-5:], start=len(todas)-4):
        log.info("  row %d: A=%r B=%r C=%r", i, (fila[0] or "")[:30], (fila[1] or "")[:50], (fila[2] or "")[:50])
    # Probar append simple para ver si funciona
    log.info("==Test append: agregando una fila DUMMY")
    test_row = ["TEST_SKU_DEL", "TEST nombre del", "TEST descripcion"]
    res = ws.append_rows([test_row], value_input_option="USER_ENTERED")
    log.info("  append response: %s", res)
    # Releer y ver si esta la fila DUMMY
    todas2 = ws.get_all_values()
    log.info("==Despues del append, len=%d", len(todas2))
    last = todas2[-1] if todas2 else []
    log.info("  ultima fila: A=%r B=%r", (last[0] if last else "")[:40], (last[1] if len(last) > 1 else "")[:40])
    # Buscar la TEST fila para ver donde quedo
    for i, fila in enumerate(todas2, start=1):
        if (fila[0] or "").startswith("TEST_SKU_DEL"):
            log.info("  TEST encontrada en fila %d", i)
            break
    # Borrar la fila TEST si la encontramos
    for i, fila in enumerate(todas2, start=1):
        if (fila[0] or "").startswith("TEST_SKU_DEL"):
            ws.delete_rows(i)
            log.info("  TEST borrada de fila %d", i)
            break


def cleanup_garbage() -> None:
    """Borra TODAS las celdas (incluso fuera de A:AH) desde la fila 3797 hasta el final.
    Sirve para limpiar la basura que dejo el bug de append_rows en columnas AJ-BO."""
    ws = _conectar()
    last_col = _col_letter(ws.col_count)
    rg = f"A3797:{last_col}{ws.row_count}"
    log.info("Limpiando celdas en %s (basura del bug de append_rows)", rg)
    ws.batch_clear([rg])
    log.info("Listo. Verificando resultado...")
    todas = ws.get_all_values()
    log.info("Despues del cleanup, len(get_all_values)=%d", len(todas))


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("highlight", "reset", "debug", "cleanup"):
        print(__doc__)
        return 1
    if sys.argv[1] == "highlight":
        highlight()
    elif sys.argv[1] == "reset":
        reset()
    elif sys.argv[1] == "cleanup":
        cleanup_garbage()
    else:
        debug()
    return 0


if __name__ == "__main__":
    sys.exit(main())
