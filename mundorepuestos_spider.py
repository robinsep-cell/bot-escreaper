"""
Spider para mundorepuestos.com

Estrategia: el sitio es ASP.NET MVC server-rendered, NO una SPA.
El sitemap index expone tres archivos (productos1.xml, productos2.xml, productos3.xml)
con ~134.000 URLs canónicas de productos. Cada ficha trae JSON-LD schema.org/Product
y Open Graph completo en el <head>, asi que basta requests + BS4:
cero JavaScript, cero Playwright.

Uso:
    from mundorepuestos_spider import iter_products
    for record in iter_products(workers=20, limit=None):
        upsert_supabase(record)
"""
from __future__ import annotations

import json
import re
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterator, Optional

import requests
from bs4 import BeautifulSoup

BASE = "https://mundorepuestos.com"
SITEMAP_INDEX = f"{BASE}/sitemap.xml"
PRODUCT_SITEMAPS = [
    f"{BASE}/productos1.xml",
    f"{BASE}/productos2.xml",
    f"{BASE}/productos3.xml",
]

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "es-CL,es;q=0.9"}

log = logging.getLogger("mundorepuestos")


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def discover_product_urls(session: Optional[requests.Session] = None) -> list[str]:
    """Descarga los 3 sitemaps de productos y devuelve la lista total de URLs."""
    sess = session or _new_session()
    urls: list[str] = []
    loc_re = re.compile(r"<loc>\s*(https?://[^\s<]+)\s*</loc>", re.I)
    for sm in PRODUCT_SITEMAPS:
        r = sess.get(sm, timeout=30)
        r.raise_for_status()
        urls.extend(m.strip() for m in loc_re.findall(r.text))
    return urls


_OG_RE = re.compile(r'<meta\s+(?:property|name)="([^"]+)"\s+content="([^"]*)"', re.I)
_JSONLD_RE = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I
)


def _extract_jsonld_product(html: str) -> Optional[dict]:
    """Devuelve el primer bloque JSON-LD de tipo Product, o None."""
    for raw in _JSONLD_RE.findall(html):
        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if isinstance(item, dict) and item.get("@type") == "Product":
                return item
    return None


def parse_product_html(html: str, url: str) -> Optional[dict]:
    """Extrae un registro estandarizado desde el HTML de una ficha de producto."""
    meta: dict[str, str] = {}
    for k, v in _OG_RE.findall(html):
        meta.setdefault(k.lower(), v)

    ld = _extract_jsonld_product(html) or {}

    if not (ld.get("name") or meta.get("og:title")):
        return None

    # ID e slug se extraen del path: /producto/{idLargo}/{slug}
    m = re.search(r"/[Pp]roducto/(\d+)/([^/?#]+)", url)
    long_id = m.group(1) if m else None
    slug = m.group(2) if m else None

    # Precio: JSON-LD lo trae como entero limpio, og:meta como string formateado
    offers = ld.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    precio_ld = offers.get("price") if isinstance(offers, dict) else None
    precio_og = meta.get("product:price:amount")

    try:
        if precio_ld is not None:
            precio = int(float(precio_ld))
        elif precio_og:
            precio = int(precio_og.replace(".", "").replace(",", ""))
        else:
            precio = None
    except (ValueError, TypeError):
        precio = None

    # Disponibilidad: JSON-LD usa schema.org/InStock, og:meta usa "in stock"
    availability_ld = offers.get("availability") if isinstance(offers, dict) else None
    availability_og = meta.get("product:availability")
    if availability_ld:
        disponible = "InStock" in availability_ld
    elif availability_og:
        disponible = availability_og == "in stock"
    else:
        disponible = None

    brand = ld.get("brand") or {}
    if isinstance(brand, dict):
        marca = brand.get("name")
    else:
        marca = brand if isinstance(brand, str) else None

    categoria = _extract_breadcrumb_categoria(html)

    return {
        "proveedor": "mundorepuestos",
        "id_proveedor": long_id,
        "sku": ld.get("sku"),
        "slug": slug,
        "url": meta.get("og:url") or url,
        "nombre": ld.get("name") or _strip_site_suffix(_decode_entities(meta.get("og:title"))),
        "descripcion": ld.get("description") or _decode_entities(meta.get("og:description")),
        "marca": marca,
        "categoria": categoria,
        "imagen": ld.get("image") or meta.get("og:image"),
        "precio_clp": precio,
        "moneda": (offers.get("priceCurrency") if isinstance(offers, dict) else None)
        or meta.get("product:price:currency")
        or "CLP",
        "disponible": disponible,
        "condicion": meta.get("product:condition"),
    }


def _extract_breadcrumb_categoria(html: str) -> Optional[str]:
    """Extrae la categoría principal del breadcrumb microdata (nivel 2: 'Carrocería', 'Motor', ...)."""
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select('[itemprop="itemListElement"]')
    names = []
    for it in items:
        n = it.select_one('[itemprop="name"]')
        if n:
            names.append(n.get_text(strip=True))
    # nivel 0 = "Inicio", nivel 1 = categoría raíz (Carrocería/Motor/etc.)
    if len(names) >= 2 and names[0].lower() in ("inicio", "home"):
        return names[1]
    return names[0] if names else None


def _decode_entities(s: Optional[str]) -> Optional[str]:
    if not s:
        return s
    import html as _html
    return _html.unescape(s).strip()


def _strip_site_suffix(s: Optional[str]) -> Optional[str]:
    """Quita ' | ... - Mundo Repuestos Chile' del final del og:title."""
    if not s:
        return s
    return re.split(r"\s*\|\s*", s, 1)[0].strip()


def fetch_product(url: str, session: requests.Session, retries: int = 3) -> Optional[dict]:
    backoff = 1.0
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 200:
                return parse_product_html(r.text, url)
            if r.status_code in (429, 503):
                time.sleep(backoff)
                backoff *= 2
                continue
            log.warning("HTTP %s en %s", r.status_code, url)
            return None
        except requests.RequestException as e:
            log.warning("excepcion %s (intento %s) en %s", e, attempt + 1, url)
            time.sleep(backoff)
            backoff *= 2
    return None


def iter_products(workers: int = 20, limit: Optional[int] = None) -> Iterator[dict]:
    """Generator: produce registros de productos en paralelo."""
    session = _new_session()
    urls = discover_product_urls(session)
    if limit:
        urls = urls[:limit]
    log.info("Total URLs a procesar: %d", len(urls))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_product, u, session): u for u in urls}
        for fut in as_completed(futures):
            rec = fut.result()
            if rec:
                yield rec


if __name__ == "__main__":
    import json, sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    print(f"Probando con {n} productos del sitemap...")
    for i, rec in enumerate(iter_products(workers=10, limit=n), 1):
        print(f"\n--- {i} ---")
        print(json.dumps(rec, ensure_ascii=False, indent=2))
