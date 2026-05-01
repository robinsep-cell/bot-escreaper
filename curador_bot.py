"""
Bot capturador del curador (Fase 1).

Flujo:
1. Lee la pestaña 'pegar' del Google Sheet
2. Para cada fila con URL y sin Estado, fetchea el producto del origen
3. Detecta duplicados por url_origen y product_id_origen contra Supabase
4. Inserta en `productos_curados` con costo total + tipo_cambio
5. Calcula precio_venta_clp via Robot B (formula del usuario)
6. Escribe estado/titulo/precio de vuelta en el Sheet

Cron sugerido: cada 10 min (workflow GitHub Actions).
"""
from __future__ import annotations

import datetime
import logging
import sys
import time
from typing import Optional

import gspread

import curador_config as cfg
import curador_fetch as fetcher

log = logging.getLogger("curador_bot")

COL_URL = 1       # A
COL_ESTADO = 2    # B
COL_TITULO = 3    # C
COL_PRECIO_USD = 4  # D
COL_PRECIO_CLP = 5  # E
COL_NOTAS = 6     # F (lee del usuario)
COL_VEHICULOS = 7 # G (lee del usuario)


def upsert_log(sheet, accion: str, url: str, mensaje: str) -> None:
    try:
        log_ws = sheet.worksheet(cfg.SHEET_TAB_LOG)
    except gspread.WorksheetNotFound:
        log_ws = sheet.add_worksheet(title=cfg.SHEET_TAB_LOG, rows=2, cols=4)
        log_ws.update("A1:D1", [["Timestamp", "Accion", "URL", "Mensaje"]])
    ts = datetime.datetime.utcnow().isoformat(timespec="seconds")
    log_ws.append_row([ts, accion, url, mensaje], value_input_option="USER_ENTERED")


def find_existing(sb, url: str, product_id: Optional[str], fuente: str) -> Optional[dict]:
    # match por URL exacta
    r = sb.table(cfg.TABLE_CURADOS).select("id,url_origen,titulo").eq("url_origen", url).limit(1).execute()
    if r.data:
        return r.data[0]
    # match por product_id_origen + fuente
    if product_id:
        r = (
            sb.table(cfg.TABLE_CURADOS)
            .select("id,url_origen,titulo")
            .eq("fuente", fuente)
            .eq("product_id_origen", product_id)
            .limit(1)
            .execute()
        )
        if r.data:
            return r.data[0]
    return None


def insert_curado(sb, datos: dict) -> dict:
    r = sb.table(cfg.TABLE_CURADOS).insert(datos).execute()
    return r.data[0] if r.data else {}


def process_row(sb, sheet, ws, row_index: int, row_values: list[str]) -> None:
    url = (row_values[COL_URL - 1] if len(row_values) >= COL_URL else "").strip()
    estado_actual = (row_values[COL_ESTADO - 1] if len(row_values) >= COL_ESTADO else "").strip()
    if not url or estado_actual:
        return
    notas = (row_values[COL_NOTAS - 1] if len(row_values) >= COL_NOTAS else "").strip() or None
    vehiculos = (row_values[COL_VEHICULOS - 1] if len(row_values) >= COL_VEHICULOS else "").strip() or None

    log.info("fila %d -> %s", row_index, url[:80])

    fuente = fetcher.detect_fuente(url)
    if not fuente:
        ws.update_cell(row_index, COL_ESTADO, "ERROR: dominio no soportado")
        upsert_log(sheet, "skip", url, "dominio no soportado")
        return

    pid = (
        fetcher.extract_aliexpress_product_id(url)
        if fuente == "aliexpress"
        else fetcher.extract_ebay_item_id(url)
    )

    # dedup
    existing = find_existing(sb, url, pid, fuente)
    if existing:
        ws.update_cell(row_index, COL_ESTADO, f"DUPLICADO id={existing['id']}")
        ws.update_cell(row_index, COL_TITULO, existing.get("titulo") or "")
        upsert_log(sheet, "duplicado", url, f"id existente: {existing['id']}")
        return

    # fetch
    data = fetcher.fetch_any(url)
    if not data or not data.get("titulo"):
        ws.update_cell(row_index, COL_ESTADO, "ERROR: no pude extraer ficha")
        upsert_log(sheet, "fetch_fail", url, "fetcher devolvio None o sin titulo")
        return

    # calculo de costo + precio venta
    precio_usd = float(data.get("precio_origen_usd") or 0)
    envio_usd = float(data.get("envio_usd") or 0)
    impuesto_usd = (precio_usd + envio_usd) * (cfg.IVA_PCT / 100.0)
    costo_total_usd = precio_usd + envio_usd + impuesto_usd
    tipo_cambio = cfg.get_usd_clp()
    costo_total_clp = costo_total_usd * tipo_cambio
    precio_venta, mult = cfg.precio_venta_clp(costo_total_clp)

    payload = {
        "fuente": fuente,
        "url_origen": url,
        "product_id_origen": pid,
        "titulo": data["titulo"],
        "imagen_url": data.get("imagen_url"),
        "vendedor": data.get("vendedor"),
        "rating_vendedor": data.get("rating_vendedor"),
        "precio_origen_usd": precio_usd or None,
        "envio_usd": envio_usd,
        "impuesto_pct": cfg.IVA_PCT,
        "costo_total_usd": costo_total_usd,
        "tipo_cambio_clp": tipo_cambio,
        "costo_total_clp": costo_total_clp,
        "precio_venta_clp": precio_venta,
        "multiplicador_aplicado": mult,
        "notas": notas,
        "vehiculos_compatibles": vehiculos,
        "ultima_revision": datetime.datetime.utcnow().isoformat(),
    }
    inserted = insert_curado(sb, payload)

    ws.update_cell(row_index, COL_ESTADO, f"OK id={inserted.get('id', '?')}")
    ws.update_cell(row_index, COL_TITULO, data["titulo"][:120])
    ws.update_cell(row_index, COL_PRECIO_USD, round(costo_total_usd, 2))
    ws.update_cell(row_index, COL_PRECIO_CLP, int(precio_venta))
    upsert_log(sheet, "ok", url, f"insertado id={inserted.get('id','?')}, PV={int(precio_venta):,}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg.fail_if_missing("SUPABASE_URL", "SUPABASE_KEY", "CURADOR_SHEET_ID")

    sb = cfg.get_supabase()
    sheet = cfg.get_sheet()
    try:
        ws = sheet.worksheet(cfg.SHEET_TAB_PEGAR)
    except gspread.WorksheetNotFound:
        log.error("Falta la pestaña '%s' en el Sheet", cfg.SHEET_TAB_PEGAR)
        return 1

    rows = ws.get_all_values()  # incluye header
    log.info("Filas en sheet: %d", len(rows))
    procesadas = 0
    for idx, row in enumerate(rows[1:], start=2):  # saltar header
        try:
            before = procesadas
            process_row(sb, sheet, ws, idx, row)
            time.sleep(1)  # gentle con AliExpress
            procesadas += 1 if before != procesadas else 0
        except Exception as e:
            log.exception("error en fila %d: %s", idx, e)
            try:
                ws.update_cell(idx, COL_ESTADO, f"ERROR: {str(e)[:80]}")
            except Exception:
                pass
    log.info("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
