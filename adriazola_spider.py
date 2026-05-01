"""
Spider para adriazolarepuestos.com

Estrategia: sitio custom (Bootstrap 4 + jQuery), server-rendered, NO SPA.
Cada pagina de catalogo (`/m/{marca}` o `/c/{cat}/{subcat}` o `/r/{rubro}/{sub}`)
muestra hasta 10 tarjetas .entry con todos los campos en HTML directo.
Paginacion: `?desde=N` en pasos de 10. Cero JavaScript necesario.

Uso:
    from adriazola_spider import iter_products
    for record in iter_products():
        upsert_supabase(record)
"""
from __future__ import annotations

import re
import time
import logging
from typing import Iterator, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.adriazolarepuestos.com"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "es-CL,es;q=0.9"}
PAGE_SIZE = 10

log = logging.getLogger("adriazola")


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def discover_catalog_roots(session: Optional[requests.Session] = None) -> list[str]:
    """Descubre todas las URLs de catalogo (rubros + categorias) desde la home."""
    sess = session or _new_session()
    r = sess.get(f"{BASE}/", timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    paths: set[str] = set()
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if href.startswith(("/r/", "/c/", "/m/")):
            # Quita query strings para tomar la raiz del catalogo
            paths.add(href.split("?", 1)[0])
    # Devuelve solo las hojas mas profundas (las leaf categories) para no duplicar trabajo;
    # si una URL es prefijo de otra, la mas larga la incluye.
    sorted_paths = sorted(paths, key=len, reverse=True)
    leaves: list[str] = []
    for p in sorted_paths:
        if not any(o.startswith(p.rstrip("/") + "/") for o in leaves):
            leaves.append(p)
    return [urljoin(BASE, p) for p in leaves]


_PRICE_RE = re.compile(r"[\d.]+")


def _text(el) -> Optional[str]:
    return el.get_text(strip=True) if el else None


def _categoria_from_root(root_url: str) -> str:
    """Deriva nombre de categoria desde la URL del root: '/r/carroceria/retrovisores' -> 'Retrovisores'."""
    path = root_url.rstrip("/").split("/")
    leaf = path[-1] if path else ""
    return leaf.replace("-", " ").title() if leaf else "General"


def parse_catalog_page(html: str, source_url: str, categoria: Optional[str] = None) -> list[dict]:
    """Extrae todas las tarjetas .entry de una pagina de catalogo."""
    soup = BeautifulSoup(html, "html.parser")
    entries = soup.select("div.entry")
    out: list[dict] = []
    if categoria is None:
        categoria = _categoria_from_root(source_url.split("?", 1)[0])
    for e in entries:
        data_url = e.get("data-url", "")
        m = re.search(r"/app/rep/(\d+)", data_url)
        internal_id = m.group(1) if m else None

        # ID y precio numerico tambien estan en hidden inputs del form de cotizacion
        id_input = e.select_one('input[name="id"]')
        if not internal_id and id_input:
            internal_id = id_input.get("value")
        precio_input = e.select_one('input[name="precio"]')
        precio_clp = int(precio_input["value"]) if precio_input and precio_input.get("value", "").isdigit() else None

        nombre = _text(e.select_one("h2 a"))
        sku = _text(e.select_one(".col.info .codigo"))  # codigo web Wnnnnnn
        marca = _text(e.select_one(".col.info .marca"))
        origen = _text(e.select_one(".col.info .origen"))
        precio_fmt = _text(e.select_one(".precio"))
        imagen_el = e.select_one("img.imagen")
        imagen = imagen_el["src"] if imagen_el and imagen_el.has_attr("src") else None
        if imagen and imagen.startswith("/"):
            imagen = urljoin(BASE, imagen)

        # Stock por tienda
        stocks: dict[str, int] = {}
        for row in e.select(".stocks .row.stock"):
            cols = row.select(".col-8, .col, .col-4")
            if len(cols) >= 2:
                tienda = cols[0].get_text(strip=True)
                qty_txt = cols[-1].get_text(strip=True)
                try:
                    stocks[tienda] = int(qty_txt)
                except ValueError:
                    pass
        stock_total = sum(stocks.values()) if stocks else None

        out.append({
            "proveedor": "adriazolarepuestos",
            "id_proveedor": internal_id,
            "sku": sku,
            "url": urljoin(BASE, data_url) if data_url else source_url,
            "nombre": nombre,
            "marca": marca,
            "origen": origen,
            "categoria": categoria,
            "imagen": imagen,
            "precio_clp": precio_clp,
            "precio_formato": precio_fmt,
            "moneda": "CLP",
            "stock_total": stock_total,
            "stock_por_tienda": stocks or None,
            "iva_incluido": True,
        })
    return out


def _fetch(url: str, session: requests.Session, retries: int = 3) -> Optional[str]:
    backoff = 1.0
    for _ in range(retries):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 200:
                return r.text
            if r.status_code in (429, 503):
                time.sleep(backoff); backoff *= 2; continue
            return None
        except requests.RequestException:
            time.sleep(backoff); backoff *= 2
    return None


def iter_catalog(url: str, session: requests.Session) -> Iterator[dict]:
    """Itera todas las paginas de un catalogo (rubro/categoria/marca) hasta agotar."""
    desde = 0
    seen_ids: set[str] = set()
    categoria = _categoria_from_root(url)
    while True:
        page_url = f"{url}?desde={desde}" if desde else url
        html = _fetch(page_url, session)
        if not html:
            break
        records = parse_catalog_page(html, page_url, categoria=categoria)
        if not records:
            break
        new_records = [r for r in records if r["id_proveedor"] and r["id_proveedor"] not in seen_ids]
        if not new_records:
            break
        for r in new_records:
            seen_ids.add(r["id_proveedor"])
            yield r
        if len(records) < PAGE_SIZE:
            break
        desde += PAGE_SIZE
        time.sleep(0.3)  # cortesia con el server


def iter_products(roots: Optional[list[str]] = None) -> Iterator[dict]:
    """Generator: produce todos los productos descubribles desde rubros + categorias."""
    session = _new_session()
    if roots is None:
        roots = discover_catalog_roots(session)
    log.info("Catalogos a recorrer: %d", len(roots))

    global_seen: set[str] = set()
    for root in roots:
        for rec in iter_catalog(root, session):
            pid = rec["id_proveedor"]
            if pid in global_seen:
                continue
            global_seen.add(pid)
            yield rec


if __name__ == "__main__":
    import json, sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    print(f"Probando: descubrir y extraer {n} productos...")
    count = 0
    for rec in iter_products():
        print(json.dumps(rec, ensure_ascii=False, indent=2))
        count += 1
        if count >= n:
            break
    print(f"\nTotal extraidos en demo: {count}")
