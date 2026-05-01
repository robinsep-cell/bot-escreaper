"""
Spider para ciper.cl

Plataforma: VTEX. Tiene API publica `/api/catalog_system/pub/products/search/`
que devuelve JSON completo (productId, productName, brand, items[], offers).
Cero scraping HTML, cero Playwright.

Notas:
- Requiere host `www.ciper.cl` (sin www da timeout).
- VTEX limita la query a 50 productos por request (`_from`/`_to`).
- Sin filtros, paginar avanzando _from/_to hasta recibir 0 productos.
"""
from __future__ import annotations

import logging
import time
from typing import Iterator, Optional

import requests

BASE = "https://www.ciper.cl"
SEARCH_API = f"{BASE}/api/catalog_system/pub/products/search/"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept": "application/json", "Accept-Language": "es-CL,es;q=0.9"}
PAGE_SIZE = 50  # VTEX max por request

log = logging.getLogger("ciper")


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def fetch_page(session: requests.Session, frm: int, to: int, retries: int = 3) -> Optional[list]:
    backoff = 1.0
    for _ in range(retries):
        try:
            r = session.get(SEARCH_API, params={"_from": frm, "_to": to}, timeout=30)
            if r.status_code in (200, 206):
                return r.json()
            if r.status_code in (429, 503):
                time.sleep(backoff); backoff *= 2; continue
            log.warning("ciper API HTTP %s en _from=%s", r.status_code, frm)
            return None
        except requests.RequestException as e:
            log.warning("excepcion ciper API: %s", e)
            time.sleep(backoff); backoff *= 2
    return None


def normalize_product(p: dict) -> Optional[dict]:
    """Mapea un producto VTEX al formato comun."""
    items = p.get("items") or []
    if not items:
        return None
    item = items[0]
    sellers = item.get("sellers") or []
    offer = (sellers[0].get("commertialOffer") or {}) if sellers else {}
    price = offer.get("Price")
    list_price = offer.get("ListPrice") or offer.get("PriceWithoutDiscount")
    available = bool(offer.get("IsAvailable"))
    images = item.get("images") or []
    imagen = images[0].get("imageUrl") if images else None

    categories = p.get("categories") or []
    # categories ej: ["/Carrocería/Máscaras/", "/Carrocería/"] -> usamos el primero, primer segmento
    categoria = "General"
    if categories:
        first = categories[0].strip("/").split("/")
        if first:
            categoria = first[0]

    return {
        "proveedor": "ciper",
        "id_proveedor": str(p.get("productId")),
        "sku": p.get("productReference") or p.get("productReferenceCode") or item.get("itemId"),
        "url": p.get("link"),
        "nombre": p.get("productName"),
        "marca": p.get("brand"),
        "categoria": categoria,
        "imagen": imagen,
        "precio_clp": float(price) if price is not None else 0.0,
        "precio_lista_clp": float(list_price) if list_price is not None else None,
        "moneda": "CLP",
        "disponible": available,
    }


def iter_products() -> Iterator[dict]:
    session = _new_session()
    frm = 0
    while True:
        to = frm + PAGE_SIZE - 1
        data = fetch_page(session, frm, to)
        if not data:
            log.info("ciper: fin de catalogo en _from=%s", frm)
            break
        for p in data:
            rec = normalize_product(p)
            if rec:
                yield rec
        if len(data) < PAGE_SIZE:
            break
        frm += PAGE_SIZE
        time.sleep(0.3)


if __name__ == "__main__":
    import json, sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    cnt = 0
    for rec in iter_products():
        print(json.dumps(rec, ensure_ascii=False, indent=2))
        cnt += 1
        if cnt >= n: break
    print(f"\nTotal extraidos en demo: {cnt}")
