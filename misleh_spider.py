"""
Spider para repuestosmisleh.cl

Realidad del sitio (NO necesita Playwright, NO tiene WAF agresivo):
- HTTP 200 con UA basico, server: nginx
- HTML server-rendered, productos en <article class="producto-catalogo">
- Catalogo browseable solo por marca: /repuestos/{marca} (chevrolet, ford, nissan, etc.)
- Paginacion via ?page=N (20 productos por pagina)
- Sitio NO publica precios: todo es "Cotizar via WhatsApp" -> precio=0 en BD
"""
from __future__ import annotations

import http.client
import logging
import re
import time
from typing import Iterator, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# Misleh duplica headers Set-Cookie (~22 por respuesta). Stdlib lo corta a 100; subimos el techo.
http.client._MAXHEADERS = 1000  # type: ignore[attr-defined]

BASE = "https://repuestosmisleh.cl"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "es-CL,es;q=0.9"}

log = logging.getLogger("misleh")


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def discover_brands(session: Optional[requests.Session] = None) -> list[str]:
    """Lista las URLs /repuestos/{marca} desde la home."""
    sess = session or _new_session()
    r = sess.get(f"{BASE}/", timeout=20)
    r.raise_for_status()
    paths = sorted(set(re.findall(r'href="(/repuestos/[a-z0-9-]+)"', r.text)))
    return [urljoin(BASE, p) for p in paths]


def parse_catalog_page(html: str, marca_categoria: str) -> list[dict]:
    """Extrae todas las tarjetas <article class='producto-catalogo'> de una pagina."""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.find_all("article", class_=lambda c: c and "producto-catalogo" in c)
    out: list[dict] = []
    for c in cards:
        a = c.find("a", href=True)
        url = a["href"] if a else None
        if not url:
            continue
        title = c.select_one("h2.titulo-producto, .titulo-producto")
        nombre = title.get_text(strip=True) if title else None
        # SKU: el span.font-weight-normal dentro del bloque sku-producto
        sku = None
        sku_block = c.find("span", class_=lambda c: c and "sku-producto" in c)
        if sku_block:
            inner = sku_block.find("span", class_="font-weight-normal")
            if inner:
                sku = inner.get_text(strip=True)
        # Imagen
        img = c.find("img")
        imagen = img["src"] if img and img.get("src") else None
        if imagen and not imagen.lower().endswith(("default.png",)):
            pass  # imagen real
        # Disponibilidad
        disp_el = c.find("span", class_="text-success")
        disponible = bool(disp_el and "disponible" in disp_el.get_text(strip=True).lower())
        out.append({
            "proveedor": "repuestosmisleh",
            "sku": sku,
            "url": url,
            "nombre": nombre,
            "imagen": imagen,
            "categoria": marca_categoria,
            "precio_clp": 0.0,  # Misleh no publica precios; quedan como cotizacion
            "moneda": "CLP",
            "disponible": disponible,
        })
    return out


def _fetch(url: str, session: requests.Session, retries: int = 3) -> Optional[str]:
    backoff = 1.0
    for _ in range(retries):
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 200:
                return r.text
            if r.status_code in (429, 503):
                time.sleep(backoff); backoff *= 2; continue
            return None
        except requests.RequestException:
            time.sleep(backoff); backoff *= 2
    return None


def iter_brand(brand_url: str, session: requests.Session) -> Iterator[dict]:
    """Itera todas las paginas ?page=N de una marca hasta agotar."""
    marca = brand_url.rstrip("/").split("/")[-1].title()
    page = 1
    seen: set[str] = set()
    while True:
        page_url = brand_url if page == 1 else f"{brand_url}?page={page}"
        html = _fetch(page_url, session)
        if not html:
            break
        records = parse_catalog_page(html, marca_categoria=marca)
        if not records:
            break
        new = [r for r in records if r["url"] and r["url"] not in seen]
        if not new:
            break
        for r in new:
            seen.add(r["url"])
            yield r
        page += 1
        time.sleep(0.4)


def iter_products() -> Iterator[dict]:
    session = _new_session()
    brands = discover_brands(session)
    log.info("Marcas a recorrer: %d", len(brands))
    global_seen: set[str] = set()
    for b in brands:
        for rec in iter_brand(b, session):
            if rec["url"] in global_seen:
                continue
            global_seen.add(rec["url"])
            yield rec


if __name__ == "__main__":
    import json, sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    print(f"Probando: {n} productos...")
    cnt = 0
    for rec in iter_products():
        print(json.dumps(rec, ensure_ascii=False, indent=2))
        cnt += 1
        if cnt >= n: break
    print(f"\nTotal extraidos en demo: {cnt}")
