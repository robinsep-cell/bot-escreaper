"""
Robot B: Recalcula precio_venta_clp aplicando la formula del usuario.

Util para cuando cambies la formula (ej. nuevo umbral o multiplicador) y querés
re-aplicarla a TODOS los productos curados sin esperar al vigilante diario.
"""
from __future__ import annotations

import datetime
import logging
import sys

import curador_config as cfg

log = logging.getLogger("calculador")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg.fail_if_missing("SUPABASE_URL", "SUPABASE_KEY")
    sb = cfg.get_supabase()

    rows = sb.table(cfg.TABLE_CURADOS).select("id,costo_total_clp,precio_venta_clp").eq("activo", True).execute()
    n = 0
    for prod in rows.data or []:
        if not prod.get("costo_total_clp"):
            continue
        nuevo_pv, mult = cfg.precio_venta_clp(prod["costo_total_clp"])
        if abs(nuevo_pv - (prod.get("precio_venta_clp") or 0)) < 1:
            continue
        sb.table(cfg.TABLE_CURADOS).update({
            "precio_venta_clp": nuevo_pv,
            "multiplicador_aplicado": mult,
            "ultima_revision": datetime.datetime.utcnow().isoformat(),
        }).eq("id", prod["id"]).execute()
        n += 1
        log.info("id=%s costo=%s -> PV=%s (mult=%s)", prod["id"], prod["costo_total_clp"], int(nuevo_pv), mult)
    log.info("recalculados: %d", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
