"""
Runner de inyección a Supabase usando el pipeline existente del usuario.

Mapea los registros ricos de los spiders al schema de la tabla `productos_proveedores`:
    id, proveedor, categoria, url, nombre, precio (float), imagen, fecha_actualizacion

Upsert por `url` (UNIQUE constraint). Idempotente: re-correr solo refresca.

Uso:
    python run_to_supabase.py adriazola             # full run adriazola
    python run_to_supabase.py mundo                 # full run mundo
    python run_to_supabase.py adriazola --limit 5   # smoke test
    python run_to_supabase.py mundo --limit 10      # smoke test
"""
from __future__ import annotations

import argparse
import datetime
import itertools
import logging
import os
import sys
import time
from typing import Iterable, Iterator, Optional

from supabase import Client, create_client

import adriazola_spider
import ciper_spider
import misleh_spider
import mundorepuestos_spider

# Credenciales por env var (GitHub Secrets en cloud, EnvironmentVariables en launchd local)
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
TABLE = os.environ.get("SUPABASE_TABLE", "productos_proveedores")

if not SUPABASE_URL or not SUPABASE_KEY:
    sys.exit(
        "ERROR: faltan SUPABASE_URL y/o SUPABASE_KEY como variables de entorno.\n"
        "  Local: agregalas al EnvironmentVariables del launchd plist.\n"
        "  GitHub Actions: configurarlas en Settings > Secrets and variables > Actions."
    )

# Strings de proveedor consistentes con los que el supervisor ya espera
PROVEEDOR_MUNDO = "Mundo Repuestos"
PROVEEDOR_ADRIAZOLA = "Adriazola Repuestos"
PROVEEDOR_MISLEH = "Repuestos Misleh"
PROVEEDOR_CIPER = "CIPER"
PROVEEDOR_VIGFOR = "Vigfor"

BATCH_SIZE = 200

log = logging.getLogger("supabase_runner")


def now_utc_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def to_db_row(rec: dict, proveedor_label: str) -> Optional[dict]:
    """Mapea un record del spider al schema de la tabla `productos_proveedores`."""
    url = rec.get("url")
    nombre = rec.get("nombre")
    precio = rec.get("precio_clp")
    if not url or not nombre or precio is None:
        return None
    return {
        "proveedor": proveedor_label,
        "categoria": rec.get("categoria") or "General",
        "url": url,
        "nombre": nombre,
        "precio": float(precio),
        "imagen": rec.get("imagen") or "",
        "fecha_actualizacion": now_utc_iso(),
    }


def chunked(it: Iterable[dict], n: int) -> Iterator[list[dict]]:
    batch: list[dict] = []
    for x in it:
        batch.append(x)
        if len(batch) >= n:
            yield batch
            batch = []
    if batch:
        yield batch


def upsert_batch(sb: Client, rows: list[dict]) -> int:
    """Upsert con dedupe por URL (la tabla tiene UNIQUE en url; Supabase requiere unicidad dentro del batch)."""
    seen: dict[str, dict] = {}
    for r in rows:
        seen[r["url"]] = r  # ultima version gana
    payload = list(seen.values())
    sb.table(TABLE).upsert(payload, on_conflict="url").execute()
    return len(payload)


def run_provider(name: str, records: Iterable[dict], proveedor_label: str, limit: Optional[int] = None) -> None:
    sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    log.info("[%s] iniciando upsert a %s/%s", name, SUPABASE_URL, TABLE)

    rows: Iterator[dict] = (to_db_row(r, proveedor_label) for r in records)
    rows = (r for r in rows if r is not None)
    if limit:
        rows = itertools.islice(rows, limit)  # short-circuit real, no solo filtro

    total = 0
    started = time.time()
    for batch in chunked(rows, BATCH_SIZE):
        try:
            n = upsert_batch(sb, batch)
            total += n
            elapsed = time.time() - started
            rate = total / elapsed if elapsed else 0
            log.info("[%s] +%d (total=%d, %.1f/s)", name, n, total, rate)
        except Exception as e:
            log.error("[%s] error en batch: %s", name, e)
            time.sleep(2)

    elapsed = time.time() - started
    log.info("[%s] completado: %d filas en %.1fs", name, total, elapsed)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("provider", choices=["adriazola", "mundo", "misleh", "ciper", "vigfor"])
    parser.add_argument("--limit", type=int, default=None, help="Smoke test: limita N registros")
    parser.add_argument("--workers", type=int, default=20, help="Solo aplica a mundo")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    if args.provider == "adriazola":
        run_provider("adriazola", adriazola_spider.iter_products(), PROVEEDOR_ADRIAZOLA, limit=args.limit)
    elif args.provider == "mundo":
        run_provider(
            "mundo",
            mundorepuestos_spider.iter_products(workers=args.workers, limit=args.limit),
            PROVEEDOR_MUNDO,
            limit=None,  # el limit se aplica adentro de iter_products para no descargar de mas
        )
    elif args.provider == "misleh":
        run_provider("misleh", misleh_spider.iter_products(), PROVEEDOR_MISLEH, limit=args.limit)
    elif args.provider == "ciper":
        run_provider("ciper", ciper_spider.iter_products(), PROVEEDOR_CIPER, limit=args.limit)
    elif args.provider == "vigfor":
        import vigfor_spider  # import perezoso: requiere Playwright instalado
        run_provider("vigfor", vigfor_spider.iter_products(), PROVEEDOR_VIGFOR, limit=args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
