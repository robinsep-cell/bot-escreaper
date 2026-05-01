"""
Robot A: Vigilante de precios origen.

Para cada producto curado activo:
1. Re-fetchea desde el origen
2. Compara precio nuevo vs anterior
3. Inserta una fila en `precio_historial`
4. Actualiza `precio_origen_usd`, `costo_total_*`, `precio_venta_clp` en `productos_curados`
5. Marca alerta si cambio > umbral (default 10%)

Cron sugerido: diario 6am UTC (3am Chile, fuera de horas pico).
"""
from __future__ import annotations

import datetime
import logging
import sys
import time
from typing import Optional

import curador_config as cfg
import curador_fetch as fetcher

log = logging.getLogger("vigilante")
UMBRAL_PCT = float(__import__("os").environ.get("ALERTA_PRECIO_PCT", "10"))


def revisar_uno(sb, prod: dict, tipo_cambio: float) -> None:
    url = prod["url_origen"]
    log.info("refresh id=%s url=%s", prod["id"], url[:80])
    data = fetcher.fetch_any(url)
    if not data:
        log.warning("  fetch fallo")
        return
    nuevo_precio_usd = float(data.get("precio_origen_usd") or 0)
    if not nuevo_precio_usd:
        log.warning("  sin precio extraido, skip")
        return
    nuevo_envio_usd = float(data.get("envio_usd") or 0)
    impuesto_usd = (nuevo_precio_usd + nuevo_envio_usd) * (cfg.IVA_PCT / 100.0)
    nuevo_costo_usd = nuevo_precio_usd + nuevo_envio_usd + impuesto_usd
    nuevo_costo_clp = nuevo_costo_usd * tipo_cambio
    nuevo_pv, mult = cfg.precio_venta_clp(nuevo_costo_clp)

    # historial
    sb.table(cfg.TABLE_HISTORIAL).insert({
        "producto_id": prod["id"],
        "precio_origen_usd": nuevo_precio_usd,
        "envio_usd": nuevo_envio_usd,
        "costo_total_clp": nuevo_costo_clp,
        "tipo_cambio_clp": tipo_cambio,
        "precio_venta_clp": nuevo_pv,
    }).execute()

    # detectar cambio
    viejo_pv = float(prod.get("precio_venta_clp") or 0)
    if viejo_pv:
        delta_pct = abs((nuevo_pv - viejo_pv) / viejo_pv) * 100
    else:
        delta_pct = 0
    alerta = delta_pct >= UMBRAL_PCT

    sb.table(cfg.TABLE_CURADOS).update({
        "precio_origen_usd": nuevo_precio_usd,
        "envio_usd": nuevo_envio_usd,
        "costo_total_usd": nuevo_costo_usd,
        "tipo_cambio_clp": tipo_cambio,
        "costo_total_clp": nuevo_costo_clp,
        "precio_venta_clp": nuevo_pv,
        "multiplicador_aplicado": mult,
        "ultima_revision": datetime.datetime.utcnow().isoformat(),
        "cambio_precio_alert": alerta,
    }).eq("id", prod["id"]).execute()

    log.info("  PV viejo=%s nuevo=%s delta=%.1f%% alert=%s", viejo_pv, int(nuevo_pv), delta_pct, alerta)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg.fail_if_missing("SUPABASE_URL", "SUPABASE_KEY")
    sb = cfg.get_supabase()
    tipo_cambio = cfg.get_usd_clp()
    log.info("USD/CLP usado: %s", tipo_cambio)

    # listar todos los activos
    rows = sb.table(cfg.TABLE_CURADOS).select("*").eq("activo", True).execute()
    log.info("activos a revisar: %d", len(rows.data or []))
    for prod in rows.data or []:
        try:
            revisar_uno(sb, prod, tipo_cambio)
            time.sleep(2)  # gentle
        except Exception as e:
            log.exception("fallo id=%s: %s", prod.get("id"), e)
    log.info("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
