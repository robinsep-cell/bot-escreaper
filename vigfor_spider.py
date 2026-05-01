"""
Spider para vigfor.cl

Plataforma: SPA propietaria (Discospare/Lupo) detras de Cloudflare.
Estrategia confirmada por el usuario: las categorias del menu lateral exponen
catalogo browseable directo (10 productos por pagina, paginacion via ?p={token}).

Cada tarjeta es un <div class="product-card product-card-go-detail pointer"> con:
- data-id="{ProductId}"
- href="/{slug}/{ProductId}"
- button[name="btnAddCart"][data-json="{...}"] -> JSON con TODOS los campos:
  ProductId, ProductPrice (decimal), ProductStock, ProductPartNumber, ProductDetails

Cloudflare challenge: Playwright lo resuelve solo (el CF cookie persiste en el contexto).

Uso:
    # Crawl de las 2 categorias que el usuario priorizo
    from vigfor_spider import iter_products, ESPEJOS_CATEGORIES
    for rec in iter_products(ESPEJOS_CATEGORIES):
        ...

    # O con tus propias categorias
    iter_products(["/espejos-espejos-retrovisores", "/vidrios-parabrisas"])
"""
from __future__ import annotations

import html as _html
import json
import logging
import re
import time
from typing import Iterator, Optional
from urllib.parse import urljoin

log = logging.getLogger("vigfor")

BASE = "https://vigfor.cl"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Categorias prioritarias (espejos) que el usuario solicito empezar.
# IMPORTANTE: las URLs requieren el token ?p=... para que vigfor renderice productos.
# Si vigfor cambia los tokens, hay que regenerarlos visitando el menu lateral con un browser
# y copiando los hrefs (vigfor los firma con el dealer).
ESPEJOS_CATEGORIES = [
    "/espejos-espejos-retrovisores?p=2CM-RP1eASEi3KVfeZhUX-wOZzR0Fpv0SoNTxeyXdeRR9ss2ysSFyOPIQK2fvUAO",
    "/espejos-luneta-espejos?p=2CM-RP1eASEi3KVfeZhUX-wOZzR0Fpv0SoNTxeyXdeRYUNyWDCwuU1OZL7JxYz9E",
]


def _wait_through_cf(page, max_seconds: int = 45) -> bool:
    deadline = time.time() + max_seconds
    while time.time() < deadline:
        title = (page.title() or "").lower()
        if "just a moment" not in title and "checking" not in title:
            return True
        time.sleep(1)
    return False


def _extract_cards(html: str, source_url: str) -> list[dict]:
    """Extrae los productos de una pagina de categoria parseando HTML y data-json."""
    out: list[dict] = []
    # Cada card empieza con <div class="product-card product-card-go-detail pointer" data-id="...">
    # Tomamos cada bloque hasta el siguiente div igual o cierre del contenedor.
    card_starts = [m for m in re.finditer(
        r'<div class="product-card product-card-go-detail pointer"[^>]*data-id="(\d+)"',
        html,
    )]
    for i, m in enumerate(card_starts):
        start = m.start()
        end = card_starts[i + 1].start() if i + 1 < len(card_starts) else min(len(html), start + 6000)
        block = html[start:end]
        product_id = m.group(1)

        # data-json en el boton btnAddCart contiene todo (precio decimal, sku, stock, etc.)
        rec = _parse_data_json(block) or {}
        rec["id_proveedor"] = rec.get("id_proveedor") or product_id

        # URL canonica al detalle
        link_m = re.search(r'class="image__body"\s+href="([^"]+)"', block) or re.search(
            r'href="(/[^"]+/' + product_id + r')"', block
        )
        rec["url"] = urljoin(BASE, link_m.group(1)) if link_m else source_url

        # Imagen
        img_m = re.search(r'<img[^>]+class="image__tag"[^>]+src="([^"]+)"', block)
        rec["imagen"] = img_m.group(1) if img_m else None

        # Tag (Original / Alternativo / etc.)
        tag_m = re.search(r'tag-badge tag-badge--\w+\s*"\s*>\s*([^<]+?)\s*</div>', block)
        if tag_m:
            rec.setdefault("tag", tag_m.group(1).strip())

        # Nombre fallback si data-json no lo trae
        if not rec.get("nombre"):
            name_m = re.search(r'class="product-card__name">.*?>\s*([^<\n][^<]*?)\s*</div>', block, re.S)
            if name_m:
                rec["nombre"] = name_m.group(1).strip()

        out.append(rec)
    return out


def _parse_data_json(block: str) -> Optional[dict]:
    """Lee el data-json del boton 'Agregar al carrito' y lo normaliza."""
    m = re.search(r'data-json="([^"]+)"', block)
    if not m:
        return None
    try:
        raw = _html.unescape(m.group(1))
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None

    price = data.get("ProductPrice") or data.get("ProductFinalDecimalPrice")
    return {
        "proveedor": "vigfor",
        "id_proveedor": str(data.get("ProductId")) if data.get("ProductId") else None,
        "sku": data.get("ProductPartNumber") or None,
        "nombre": (data.get("ProductDescription") or "").strip() or None,
        "descripcion": data.get("ProductDetails"),
        "precio_clp": float(price) if price else 0.0,
        "moneda": "CLP",
        "stock": int(data.get("ProductStock")) if data.get("ProductStock") is not None else None,
        "ask_disponibilidad": bool(data.get("ProductAskAvailability")),
    }


def _next_page_href(html: str, current_path: str) -> Optional[str]:
    """Encuentra el link 'siguiente' del paginador. None si no hay mas."""
    # rel="next" es lo limpio si esta presente
    m = re.search(r'<a[^>]*href="([^"]+)"[^>]*rel="next"', html)
    if m:
        return m.group(1)
    # Fallback: el paginador tiene <a href="...?p=X"> con texto "Siguiente"
    m = re.search(
        r'<a[^>]*href="([^"]+\?p=[^"]+)"[^>]*>\s*(?:Siguiente|Next|&raquo;|»)',
        html,
        re.I,
    )
    if m:
        return m.group(1)
    return None


def iter_category(page, category_path: str) -> Iterator[dict]:
    """Itera todas las paginas de una categoria, extrayendo productos."""
    url = urljoin(BASE, category_path)
    seen: set[str] = set()
    while True:
        log.info("vigfor: fetching %s", url)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            _wait_through_cf(page, max_seconds=20)  # por si CF intercepta la categoria
            # esperar render del SPA (productos se inyectan tras XHR interno)
            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                pass  # algunos sitios nunca llegan a idle; seguimos igual
            time.sleep(1.0)
        except Exception as e:
            log.warning("vigfor: error en %s: %s", url, e)
            return

        html = page.content()
        records = _extract_cards(html, url)
        if not records:
            log.info("vigfor: 0 productos en %s, fin.", url)
            return
        new_records = [r for r in records if r["id_proveedor"] and r["id_proveedor"] not in seen]
        if not new_records:
            log.info("vigfor: ningun producto nuevo, fin de paginacion.")
            return
        for r in new_records:
            seen.add(r["id_proveedor"])
            yield r

        nxt = _next_page_href(html, category_path)
        if not nxt:
            return
        url = urljoin(BASE, nxt)


def iter_products(
    categories: Optional[list[str]] = None,
    headless: bool = True,
    max_products: Optional[int] = None,
) -> Iterator[dict]:
    """Generator de productos crawleados desde la lista de categorias."""
    from playwright.sync_api import sync_playwright

    cats = categories if categories is not None else ESPEJOS_CATEGORIES
    yielded = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=UA, locale="es-CL", viewport={"width": 1366, "height": 900}
        )
        page = context.new_page()
        log.info("vigfor: home + CF challenge...")
        page.goto(f"{BASE}/", wait_until="domcontentloaded", timeout=60000)
        if not _wait_through_cf(page, 45):
            log.error("vigfor: CF no resuelto en 45s")
            browser.close()
            return
        log.info("vigfor: CF ok. Categorias a recorrer: %d", len(cats))

        global_seen: set[str] = set()
        for cat in cats:
            cat_name = cat.strip("/").replace("-", " ").title()
            log.info("vigfor: -> categoria %s", cat_name)
            for rec in iter_category(page, cat):
                if rec["id_proveedor"] in global_seen:
                    continue
                global_seen.add(rec["id_proveedor"])
                rec["categoria"] = cat_name
                yielded += 1
                yield rec
                if max_products and yielded >= max_products:
                    browser.close()
                    return
        browser.close()


if __name__ == "__main__":
    import json as _json, sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    cnt = 0
    for rec in iter_products(headless=True, max_products=n):
        print(_json.dumps(rec, ensure_ascii=False, indent=2))
        cnt += 1
    print(f"\nTotal extraidos en demo: {cnt}")
