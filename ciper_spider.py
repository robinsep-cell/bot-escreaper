"""
Spider para ciper.cl

Plataforma: VTEX. Antes usabamos /api/catalog_system/pub/products/search/ paginated
pero VTEX limita la paginacion lineal a ~2.500 productos. Refactor: ahora usamos
el sitemap publico de Ciper que expone TODO el catalogo (~17.500 URLs en 70 sub-sitemaps).

Estrategia:
1. Bajar /sitemap.xml -> indice
2. Bajar cada product-N.xml -> URLs canonicas de productos
3. Por cada URL hacer GET y extraer JSON-LD + meta tags Open Graph
4. JSON-LD trae name, sku, brand, image; meta tags traen price/availability
5. Insertar via run_to_supabase con upsert por url
"""
from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterator, Optional

import requests

BASE = "https://www.ciper.cl"
SITEMAP_INDEX = f"{BASE}/sitemap.xml"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "es-CL,es;q=0.9"}

log = logging.getLogger("ciper")


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def discover_product_urls(session: Optional[requests.Session] = None) -> list[str]:
    """Devuelve TODAS las URLs canonicas de productos desde sitemap index."""
    sess = session or _new_session()
    log.info("ciper: bajando sitemap index...")
    r = sess.get(SITEMAP_INDEX, timeout=30)
    r.raise_for_status()
    inner_sitemaps = re.findall(r"<loc>(https?://[^<]+/product-\d+\.xml)</loc>", r.text)
    log.info("ciper: %d sub-sitemaps de productos", len(inner_sitemaps))

    all_urls: list[str] = []
    for sm_url in inner_sitemaps:
        try:
            r = sess.get(sm_url, timeout=30)
            r.raise_for_status()
            urls = re.findall(r"<loc>(https?://[^<]+)</loc>", r.text)
            all_urls.extend(urls)
        except Exception as e:
            log.warning("ciper: error bajando %s: %s", sm_url, e)
    log.info("ciper: %d URLs de productos discoverable", len(all_urls))
    # Dedup
    return sorted(set(all_urls))


_OG_RE = re.compile(r'<meta\s+(?:property|name)="([^"]+)"\s+content="([^"]*)"', re.I | re.S)
_LD_RE = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S)


def parse_product_html(html: str, url: str) -> Optional[dict]:
    """Extrae record desde HTML de Ciper combinando JSON-LD + Open Graph + scripts."""
    meta: dict[str, str] = {}
    for k, v in _OG_RE.findall(html):
        meta.setdefault(k.lower(), v)

    # JSON-LD Product
    name = sku = image = None
    brand = None
    for raw in _LD_RE.findall(html):
        try:
            d = json.loads(raw.strip())
        except Exception:
            continue
        items = d if isinstance(d, list) else [d]
        for it in items:
            if isinstance(it, dict) and it.get("@type") == "Product":
                name = it.get("name") or name
                sku = it.get("sku") or sku
                b = it.get("brand")
                if isinstance(b, dict):
                    brand = b.get("name") or brand
                elif isinstance(b, str):
                    brand = b or brand
                img = it.get("image")
                if isinstance(img, list) and img:
                    image = img[0]
                elif isinstance(img, str):
                    image = img

    # Fallbacks desde meta tags
    if not name:
        name = meta.get("og:title")
        if name and "- CIPER" in name:
            name = name.split("- CIPER", 1)[0].strip()
    if not sku:
        sku = meta.get("product:sku") or meta.get("product:retailer_item_id")
    if not brand:
        brand = meta.get("product:brand")
    if not image:
        image = meta.get("og:image")

    # Precio: solo en meta tags o body JS
    precio = None
    raw_price = meta.get("product:price:amount")
    if raw_price:
        try:
            precio = float(raw_price)
        except ValueError:
            pass
    if precio is None:
        m = re.search(r'"Price"\s*:\s*([\d.]+)', html)
        if m:
            try:
                precio = float(m.group(1))
            except ValueError:
                pass

    # Availability
    available = None
    avail = meta.get("product:availability")
    if avail:
        available = avail.lower() in ("instock", "in stock")

    # Categoria
    categoria = "General"
    cats = [v for k, v in _OG_RE.findall(html) if k.lower() == "product:category"]
    if cats:
        first = cats[0].strip("/").split("/")
        if first:
            categoria = first[0]

    if not name:
        return None

    return {
        "proveedor": "ciper",
        "id_proveedor": meta.get("product:retailer_part_no"),
        "sku": sku,
        "url": url,
        "nombre": name,
        "marca": brand,
        "categoria": categoria,
        "imagen": image,
        "precio_clp": precio if precio is not None else 0.0,
        "moneda": "CLP",
        "disponible": available,
    }


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
            log.warning("ciper HTTP %s en %s", r.status_code, url)
            return None
        except requests.RequestException as e:
            log.warning("ciper exception %s (intento %s)", e, attempt + 1)
            time.sleep(backoff)
            backoff *= 2
    return None


def iter_products(workers: int = 15, limit: Optional[int] = None) -> Iterator[dict]:
    """Generator: yields Ciper product records crawled from sitemap."""
    session = _new_session()
    urls = discover_product_urls(session)
    if limit:
        urls = urls[:limit]
    log.info("ciper: %d URLs a procesar con %d workers", len(urls), workers)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_product, u, session): u for u in urls}
        for fut in as_completed(futures):
            rec = fut.result()
            if rec:
                yield rec


if __name__ == "__main__":
    import json as _json
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    cnt = 0
    for rec in iter_products(workers=10, limit=n):
        print(_json.dumps(rec, ensure_ascii=False, indent=2))
        cnt += 1
    print(f"\nTotal demo: {cnt}")
