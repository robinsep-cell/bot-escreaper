"""
One-shot enrichment para Repuesto Center.

Bug observado: nombres de productos son genericos ("KIT EMBRAGUE", "TAPABARRO TRAS IZQ")
sin marca/modelo/año. Por eso no aparecen al buscar "kit embrague honda 2010" etc.

Hallazgo: la pagina de detalle (WooCommerce) tiene en `og:description` exactamente
la info que falta. Ej:  "MARCA: ZX AUTO - MODELO: ADMIRAL (ANTIGUA) ZX AUTO 2007-10"

Solucion: crawlear cada URL de Repuesto Center, extraer og:description, concatenar
al nombre y UPDATE en BD. Respeta nombre_editado.

Idempotente: re-correr no hace daño. Solo actualiza si el nombre actual NO incluye ya
la info enriquecida.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests
from supabase import Client, create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TABLE = "productos_proveedores"
PROVEEDOR = "Repuesto Center"
WORKERS = 12
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"}

log = logging.getLogger("enrich_rc")


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def fetch_og_description(url: str, session: requests.Session, retries: int = 2) -> Optional[str]:
    backoff = 1.0
    for _ in range(retries):
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 200:
                m = re.search(r'<meta\s+property="og:description"\s+content="([^"]+)"', r.text)
                return m.group(1).strip() if m else None
            if r.status_code in (429, 503):
                time.sleep(backoff); backoff *= 2; continue
            return None
        except requests.RequestException:
            time.sleep(backoff); backoff *= 2
    return None


def enriquecer(sb: Client, fila: dict, session: requests.Session) -> tuple[int, str]:
    """Devuelve (id, status). status: 'updated', 'skip-noinfo', 'skip-already', 'fail'."""
    pid = fila["id"]
    url = fila["url"]
    nombre_actual = fila["nombre"] or ""

    desc = fetch_og_description(url, session)
    if not desc:
        return pid, "skip-noinfo"
    desc = desc.strip()

    # Solo agregar si el nombre actual NO incluye ya esta info
    if desc.upper() in nombre_actual.upper():
        return pid, "skip-already"

    nombre_nuevo = f"{nombre_actual} | {desc}"
    sb.table(TABLE).update({"nombre": nombre_nuevo}).eq("id", pid).execute()
    return pid, "updated"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not SUPABASE_URL or not SUPABASE_KEY:
        sys.exit("ERROR: faltan SUPABASE_URL / SUPABASE_KEY")

    sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    session = _new_session()

    log.info("buscando productos %s con nombres cortos sin enriquecer...", PROVEEDOR)
    todas: list[dict] = []
    offset = 0
    while True:
        r = (sb.table(TABLE)
             .select("id,url,nombre")
             .eq("proveedor", PROVEEDOR)
             .is_("nombre_editado", "null")
             .not_.like("nombre", "%|%")  # no las que ya tienen el separador
             .range(offset, offset + 999)
             .execute())
        if not r.data:
            break
        todas.extend(r.data)
        if len(r.data) < 1000: break
        offset += 1000
    log.info("a enriquecer: %d productos", len(todas))

    if not todas:
        log.info("nada que hacer.")
        return 0

    counts = {"updated": 0, "skip-noinfo": 0, "skip-already": 0, "fail": 0}
    started = time.time()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(enriquecer, sb, fila, session): fila for fila in todas}
        for i, fut in enumerate(as_completed(futures)):
            try:
                pid, status = fut.result()
                counts[status] = counts.get(status, 0) + 1
            except Exception as e:
                counts["fail"] = counts.get("fail", 0) + 1
                log.warning("fallo enrich: %s", e)
            if (i + 1) % 200 == 0:
                el = time.time() - started
                rate = (i + 1) / el if el else 0
                rem = (len(todas) - i - 1) / rate if rate else 0
                log.info(
                    "progreso: %d/%d  %s  (%.1f/s, ETA %.0fs)",
                    i + 1, len(todas), counts, rate, rem
                )
    el = time.time() - started
    log.info("DONE en %.0fs. %s", el, counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
