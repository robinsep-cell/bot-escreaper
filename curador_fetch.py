"""
Fetcher para productos individuales de AliExpress y eBay.
- AliExpress: Playwright a la URL de ficha (single product); extrae JSON-LD + DOM.
- eBay: Browse API si hay token (Fase 2). Por ahora, fetch HTML.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional
from urllib.parse import urlparse

log = logging.getLogger("curador_fetch")

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def detect_fuente(url: str) -> Optional[str]:
    host = urlparse(url).netloc.lower()
    if "aliexpress" in host:
        return "aliexpress"
    if "ebay." in host:
        return "ebay"
    return None


def extract_aliexpress_product_id(url: str) -> Optional[str]:
    m = re.search(r"/item/(\d+)", url)
    return m.group(1) if m else None


def extract_pdp_npi(url: str) -> dict:
    """Extrae info embebida en el parametro pdp_npi=... que AliExpress incluye en links recomendados.

    Formato observado: pdp_npi=6@dis!{currency}!{price_lista}!{price_actual}!!!{usd_lista}!{usd_actual}!@...
    Ejemplo: pdp_npi=6@dis!CLP!249630!134800!!!1841.91!994.63!@...
    Devuelve dict con keys: moneda_local, precio_lista, precio_actual, precio_lista_usd, precio_actual_usd
    """
    from urllib.parse import urlparse, parse_qs, unquote
    qs = parse_qs(urlparse(url).query)
    raw = qs.get("pdp_npi", [None])[0]
    if not raw:
        return {}
    raw = unquote(raw)  # por si vino doble-encoded
    # Formato: 6@dis!CLP!{lista_local}!{actual_local}!!!{lista_usd}!{actual_usd}!@...
    parts = raw.split("!")
    out: dict = {}
    if len(parts) >= 8:
        try:
            out["moneda_local"] = parts[1] if parts[1] else None
            out["precio_lista_local"] = float(parts[2]) if parts[2] else None
            out["precio_actual_local"] = float(parts[3]) if parts[3] else None
            out["precio_lista_usd"] = float(parts[6]) if parts[6] else None
            out["precio_actual_usd"] = float(parts[7]) if parts[7] else None
        except (ValueError, IndexError):
            pass
    return out


def extract_ebay_item_id(url: str) -> Optional[str]:
    m = re.search(r"/itm/(?:[\w-]+/)?(\d{9,15})", url)
    return m.group(1) if m else None


# ---- AliExpress -------------------------------------------------------------

def fetch_aliexpress(url: str, headless: bool = True) -> Optional[dict]:
    """Intenta extraer ficha de AliExpress.

    Estrategia en cascada:
    1) Si la URL trae pdp_npi con precio + product_id, devolvemos sin abrir browser
       (rapidisimo, evita el anti-bot). Solo nos falta el TITULO -> seguimos a Playwright.
    2) Playwright headless con UA real -> parsear HTML.
    3) Fallback: si Playwright no devolvio precio, recuperamos el de pdp_npi.
    """
    from playwright.sync_api import sync_playwright

    npi = extract_pdp_npi(url)
    log.info("ali fetch: pdp_npi=%s pid=%s", bool(npi), extract_aliexpress_product_id(url))

    html = None
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent=UA,
            locale="es-CL",
            viewport={"width": 1366, "height": 900},
        )
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            time.sleep(2)
            html = page.content()
            title_dbg = page.title()
            log.info("ali fetch: html=%d bytes, page_title=%r", len(html), title_dbg[:100])
        except Exception as e:
            log.error("ali fetch goto error: %s", e)
        browser.close()

    if not html:
        # Sin HTML: solo nos puede salvar pdp_npi (sin titulo real)
        if npi.get("precio_actual_usd"):
            return {
                "fuente": "aliexpress",
                "product_id_origen": extract_aliexpress_product_id(url),
                "titulo": f"AliExpress producto {extract_aliexpress_product_id(url)}",
                "imagen_url": None,
                "precio_origen_usd": npi["precio_actual_usd"],
                "envio_usd": 0.0,
                "vendedor": None,
                "rating_vendedor": None,
                "fallback": "pdp_npi_only",
            }
        return None

    parsed = _parse_aliexpress_html(html, url)
    if parsed:
        log.info("ali fetch parsed: title=%r price_usd=%s", (parsed.get("titulo") or "")[:60], parsed.get("precio_origen_usd"))
    else:
        log.warning("ali fetch: parser devolvio None (sin titulo ni precio extraibles)")
    return parsed


def _parse_aliexpress_html(html: str, url: str) -> Optional[dict]:
    pid = extract_aliexpress_product_id(url)

    # Intento 1: JSON-LD Product schema
    title = price_usd = image = None
    price_min_usd = price_max_usd = None
    for m in re.finditer(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if isinstance(it, dict) and it.get("@type") == "Product":
                title = it.get("name") or title
                image = (it.get("image") or [None])[0] if isinstance(it.get("image"), list) else it.get("image")
                offers = it.get("offers") or {}
                if isinstance(offers, list): offers = offers[0] if offers else {}
                # AggregateOffer: trae lowPrice/highPrice (RANGO de variantes)
                if isinstance(offers, dict):
                    if offers.get("@type") == "AggregateOffer":
                        price_min_usd = offers.get("lowPrice")
                        price_max_usd = offers.get("highPrice")
                        price_usd = price_min_usd  # default = lowest
                    else:
                        price_usd = offers.get("price") or price_usd

    # Intento 2: meta og:title / runParams JSON inline
    if not title:
        m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
        if m: title = m.group(1)
    if not image:
        m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html)
        if m: image = m.group(1)
    if not price_usd:
        # buscar bloques JSON tipo "skuActivityAmount":{"value":12.34,...}
        m = re.search(r'"skuActivityAmount"\s*:\s*\{\s*"value"\s*:\s*([\d.]+)', html)
        if m: price_usd = float(m.group(1))

    # Intento 3 (fallback agresivo): precio embebido en pdp_npi de la URL
    npi = extract_pdp_npi(url)
    if not price_usd and npi.get("precio_actual_usd"):
        price_usd = npi["precio_actual_usd"]

    if not title and not price_usd:
        return None

    try:
        price_usd = float(price_usd) if price_usd is not None else None
    except (TypeError, ValueError):
        price_usd = None

    return {
        "fuente": "aliexpress",
        "product_id_origen": pid,
        "titulo": (title or "").strip() or None,
        "imagen_url": image,
        "precio_origen_usd": price_usd,
        "precio_min_variante_usd": float(price_min_usd) if price_min_usd else None,
        "precio_max_variante_usd": float(price_max_usd) if price_max_usd else None,
        "envio_usd": 0.0,  # AliExpress shipping requires render; deja 0 por ahora
        "vendedor": None,
        "rating_vendedor": None,
    }


# ---- eBay -------------------------------------------------------------------

def fetch_ebay(url: str) -> Optional[dict]:
    """eBay HTML directo (Browse API requiere OAuth, dejado para fase 2)."""
    import requests
    from bs4 import BeautifulSoup

    eid = extract_ebay_item_id(url)
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        r.raise_for_status()
    except Exception as e:
        log.error("ebay fetch error: %s", e)
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    title = soup.select_one('meta[property="og:title"]')
    image = soup.select_one('meta[property="og:image"]')
    price_meta = soup.select_one('meta[property="product:price:amount"]')
    price = float(price_meta["content"]) if price_meta and price_meta.get("content") else None

    return {
        "fuente": "ebay",
        "product_id_origen": eid,
        "titulo": title["content"] if title and title.has_attr("content") else None,
        "imagen_url": image["content"] if image and image.has_attr("content") else None,
        "precio_origen_usd": price,
        "envio_usd": 0.0,
        "vendedor": None,
        "rating_vendedor": None,
    }


def fetch_any(url: str) -> Optional[dict]:
    """Despacha al fetcher correspondiente segun el host."""
    fuente = detect_fuente(url)
    if fuente == "aliexpress":
        return fetch_aliexpress(url)
    if fuente == "ebay":
        return fetch_ebay(url)
    log.warning("URL no reconocida: %s", url)
    return None
