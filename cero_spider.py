"""
cero_spider.py — Sincroniza catalogo de Importadora Cero (vidrios) contra
una hoja de Google Sheets.

Logica:
1. Login a new.importadoracero.cl con email + password (env vars).
2. Itera categorias [PARABRISAS, LUNETA, LATERAL] paginando todas las
   paginas del API /api/products/search?query=X&page=N.
3. Deduplica por SKU.
4. Lee la hoja Google con un Service Account.
5. Por cada producto del scrape, busca su CodigoProveedor en la hoja:
   - si existe -> actualiza SOLO PrecioCompraMayorista y PrecioListaPublico
     (si cambio).
   - si NO existe -> agrega fila nueva al final con todos los datos.
6. Si hubo productos nuevos -> notifica por Telegram.

Env vars requeridas:
    CERO_USER, CERO_PASS                  credenciales de la cuenta de Cero
    GOOGLE_SERVICE_ACCOUNT_JSON           JSON del service account (string)
    CERO_SHEET_ID                         id de la hoja de Google
    CERO_SHEET_TAB                        nombre de la pestania
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  opcional, para notificar nuevos

Uso local:
    export $(cat .env | xargs)        # o setea las vars manualmente
    python cero_spider.py             # corrida normal
    python cero_spider.py --dry-run   # no escribe en la hoja, solo reporta
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Any

import requests

# Google Sheets
import gspread
from google.oauth2.service_account import Credentials

# ============================================================
# CONFIG
# ============================================================
BASE_URL = "https://new.importadoracero.cl"
LOGIN_URL = f"{BASE_URL}/api/customers/login"
SEARCH_URL = f"{BASE_URL}/api/products/search"
CATEGORIES = ["PARABRISAS", "LUNETA", "LATERAL"]
PAGE_DELAY_SEC = 0.6  # politeness entre requests

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
)

# Mapeo: campo del API JSON  ->  encabezado exacto de la hoja
# (orden de las columnas tal como aparece en la primera fila)
SHEET_HEADERS_TO_API = {
    "SKU":                    "sku",
    "Nombre":                 "name",
    "Descripcion":            "description",
    "Grupo":                  "group",
    "Subgrupo":               "subgroup",
    "MarcaPrincipal":         "brand",
    "MarcasCompatibles":      None,        # no esta en API; deja vacio en nuevos
    "AnioDesde":              "yearFrom",
    "AnioHasta":              "yearTo",
    "Color":                  "color",
    "Medida":                 "measurement",
    "PrecioCompraMayorista":  "salePrice",  # lo que TU pagas como cliente
    "PrecioListaPublico":     "price",      # precio sugerido al publico
    "Stock":                  "stock",
    "CodigoProveedor":        "supplierCode",  # CLAVE de comparacion
    "CodigoFamilia":          "familyCode",
    "SensorLluvia":           "rainSensor",
    "SensorHumedad":          "humiditySensor",
    "Camaras":                "cameras",
    "Antena":                 "antenna",
    "Calefaccionado":         "heated",
    "Desempanante":           "defroster",
    "BandaCeramica":          "ceramicBand",
    "BotonEspejo":            "mirrorButton",
    "SoporteEspejo":          "sensorBracket",
    "ImpresionEspejo":        "mirrorPrinting",
    "Moldura":                "moulding",
    "LuzFreno":               "brakeLamp",
    "Orificio":               "hole",
    "Sunfrit":                "sunfrit",
    "Imagen1":                None,  # se llena especial desde images[]
    "Imagen2":                None,
    "Imagen3":                None,
    "Imagen4":                None,
}
COL_CLAVE = "CodigoProveedor"
COLS_PRECIOS = ["PrecioCompraMayorista", "PrecioListaPublico"]
COL_STOCK = "Stock"

# ============================================================
# Logging
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("cero")


# ============================================================
# Telegram
# ============================================================
def telegram_notify(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.info("Telegram no configurado (TELEGRAM_BOT_TOKEN/CHAT_ID), omito.")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        if r.status_code != 200:
            log.warning("Telegram HTTP %s: %s", r.status_code, r.text[:200])
    except Exception as e:
        log.warning("Telegram fallo: %s", e)


# ============================================================
# Cero scraping
# ============================================================
def login_cero(session: requests.Session, email: str, password: str) -> None:
    """Login a Importadora Cero. La cookie de sesion queda en `session`."""
    r = session.post(
        LOGIN_URL,
        json={"email": email, "password": password},
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Referer": f"{BASE_URL}/accounts/login",
            "Origin": BASE_URL,
        },
        timeout=20,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Login fallo {r.status_code}: {r.text[:300]}")
    log.info("Login OK (%s) cookies: %s", r.status_code, list(session.cookies.keys()))


def _get_with_retry(session: requests.Session, url: str, params: dict, creds: tuple[str, str],
                    max_retries: int = 4) -> requests.Response:
    """
    GET resiliente: maneja desconexiones y re-login si la sesion expira.
    creds = (email, password) por si hay que re-loguear.
    """
    last_err: Exception | None = None
    for intento in range(1, max_retries + 1):
        try:
            r = session.get(
                url, params=params,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=20,
            )
            # Sesion expirada -> reloguear y reintentar
            if r.status_code in (401, 403):
                log.warning("HTTP %s en %s, intento %d. Reloguendo...", r.status_code, params, intento)
                login_cero(session, creds[0], creds[1])
                continue
            if r.status_code == 200:
                return r
            # Otros errores -> backoff
            log.warning("HTTP %s en intento %d, body: %s", r.status_code, intento, r.text[:150])
            last_err = RuntimeError(f"HTTP {r.status_code}")
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_err = e
            log.warning("ConnectionError intento %d: %s", intento, e)
            # Forzar nueva conexion
            try: session.close()
            except Exception: pass
            session.cookies.clear_session_cookies()
            login_cero(session, creds[0], creds[1])
        # Backoff exponencial: 2s, 4s, 8s, 16s
        time.sleep(2 ** intento)
    raise RuntimeError(f"GET {url} {params} fallo despues de {max_retries} intentos: {last_err}")


def fetch_categoria(session: requests.Session, categoria: str, creds: tuple[str, str]) -> list[dict]:
    """Devuelve TODOS los productos de una categoria (pagina hasta el final)."""
    productos: list[dict] = []
    page = 1
    while True:
        r = _get_with_retry(session, SEARCH_URL, {"query": categoria, "page": page}, creds)
        data = r.json()
        chunk = data.get("products") or []
        page_count = data.get("pageCount") or 1
        productos.extend(chunk)
        log.info("  %s pag %d/%d -> %d productos (acumulado %d)",
                 categoria, page, page_count, len(chunk), len(productos))
        if page >= page_count or not chunk:
            break
        page += 1
        time.sleep(PAGE_DELAY_SEC)
    return productos


def scrape_todo(session: requests.Session, creds: tuple[str, str]) -> dict[str, dict]:
    """Recorre todas las categorias y deduplica por SKU."""
    por_sku: dict[str, dict] = {}
    for cat in CATEGORIES:
        log.info("Fetching categoria %s...", cat)
        prods = fetch_categoria(session, cat, creds)
        for p in prods:
            sku = str(p.get("sku") or "").strip()
            if not sku:
                continue
            por_sku[sku] = p  # ultima version gana (mismo producto, datos identicos)
    log.info("Total productos unicos scrapeados: %d", len(por_sku))
    return por_sku


# ============================================================
# Mapping API -> fila de hoja
# ============================================================
def producto_a_fila(p: dict, headers: list[str]) -> list[Any]:
    """Convierte un producto JSON del API en una fila ordenada segun los headers."""
    images = p.get("images") or []
    fila: list[Any] = []
    for h in headers:
        if h == "Imagen1":
            fila.append(images[0] if len(images) > 0 else "")
        elif h == "Imagen2":
            fila.append(images[1] if len(images) > 1 else "")
        elif h == "Imagen3":
            fila.append(images[2] if len(images) > 2 else "")
        elif h == "Imagen4":
            fila.append(images[3] if len(images) > 3 else "")
        elif h in SHEET_HEADERS_TO_API:
            api_key = SHEET_HEADERS_TO_API[h]
            if api_key is None:
                fila.append("")  # campo no presente en API (calculados, etc)
            else:
                v = p.get(api_key, "")
                if isinstance(v, bool):
                    fila.append("VERDADERO" if v else "FALSO")
                elif v is None:
                    fila.append("")
                else:
                    fila.append(v)
        else:
            # Encabezado desconocido (probablemente columna calculada del usuario):
            # NO la tocamos en updates, y la dejamos vacia en nuevos productos.
            fila.append("")
    return fila


# ============================================================
# Google Sheets
# ============================================================
def conectar_sheet(sheet_id: str, tab_name: str) -> "gspread.Worksheet":
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sa_json:
        sys.exit("ERROR: falta GOOGLE_SERVICE_ACCOUNT_JSON")
    creds_dict = json.loads(sa_json)
    sa_email = creds_dict.get("client_email", "(unknown)")
    log.info("Service Account: %s", sa_email)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(creds)
    try:
        sh = gc.open_by_key(sheet_id)
    except gspread.exceptions.APIError as e:
        if "PERMISSION_DENIED" in str(e) or "permission" in str(e).lower():
            sys.exit(
                f"\nERROR: el Service Account no tiene acceso a la hoja.\n"
                f"  -> Comparte la hoja con: {sa_email}\n"
                f"     dale rol 'Editor' (sin requerir notificacion).\n"
                f"  -> Hoja: https://docs.google.com/spreadsheets/d/{sheet_id}\n"
            )
        raise
    try:
        ws = sh.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        sys.exit(f"ERROR: la pestania '{tab_name}' no existe en la hoja {sheet_id}")
    log.info("Conectado a Sheet '%s' / pestania '%s'", sh.title, ws.title)
    return ws


def col_idx_to_letter(n: int) -> str:
    """1-indexed column number -> letter ('A', 'Z', 'AA', ...)."""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def sincronizar(ws, productos: dict[str, dict], dry_run: bool) -> tuple[int, int, list[dict]]:
    """
    Update prices for matching CodigoProveedor and append new ones.

    Returns: (n_actualizados, n_nuevos, lista_nuevos_para_telegram)
    """
    # Leer toda la hoja
    todas_filas = ws.get_all_values()
    if not todas_filas:
        sys.exit("ERROR: la hoja esta vacia, ni siquiera hay headers")
    headers = todas_filas[0]
    log.info("Headers detectados (%d cols): %s...", len(headers), headers[:6])

    # Validar que las columnas clave existan
    for col in [COL_CLAVE] + COLS_PRECIOS + [COL_STOCK]:
        if col not in headers:
            sys.exit(f"ERROR: la hoja no tiene la columna '{col}'")

    # Indexar headers -> indice (1-based para gspread)
    header_idx = {h: i + 1 for i, h in enumerate(headers)}
    idx_clave = header_idx[COL_CLAVE]
    idx_p1 = header_idx["PrecioCompraMayorista"]
    idx_p2 = header_idx["PrecioListaPublico"]
    idx_stock = header_idx[COL_STOCK]

    # Indexar filas existentes por CodigoProveedor (case insensitive, trim)
    # row_number es 1-based y la fila 1 es el header => filas datos arrancan en 2
    por_codigo: dict[str, dict[str, Any]] = {}
    for i, fila in enumerate(todas_filas[1:], start=2):
        # Defenderse de filas con menos columnas que el header
        codigo = (fila[idx_clave - 1] if len(fila) >= idx_clave else "").strip().upper()
        if not codigo:
            continue
        try:
            p1 = (fila[idx_p1 - 1] if len(fila) >= idx_p1 else "").strip()
            p2 = (fila[idx_p2 - 1] if len(fila) >= idx_p2 else "").strip()
            stock = (fila[idx_stock - 1] if len(fila) >= idx_stock else "").strip()
        except Exception:
            p1, p2, stock = "", "", ""
        por_codigo[codigo] = {"row": i, "p1": p1, "p2": p2, "stock": stock}

    log.info("Filas existentes con CodigoProveedor: %d", len(por_codigo))

    # Comparar contra productos del API
    updates: list[dict] = []  # batch updates {range, values}
    nuevos_filas: list[list[Any]] = []
    nuevos_meta: list[dict] = []  # para Telegram
    skipped_zero: list[str] = []  # productos donde preservamos precio historico
    skipped_new_zero: int = 0     # productos nuevos sin precio que NO agregamos

    for sku, prod in productos.items():
        codigo = (str(prod.get("supplierCode") or "")).strip().upper()
        if not codigo:
            continue  # productos sin supplierCode no los podemos matchear/insertar
        if codigo in por_codigo:
            fila_info = por_codigo[codigo]
            new_p1 = prod.get("salePrice") or 0
            new_p2 = prod.get("price") or 0
            new_stock = prod.get("stock") or 0
            # comparar como numero limpio
            old_p1 = _to_num(fila_info["p1"])
            old_p2 = _to_num(fila_info["p2"])
            old_stock = _to_num(fila_info["stock"])

            # ===== PROTECCION ANTI-CERO =====
            # Si la API devuelve precio 0 pero teniamos precio historico > 0,
            # NO sobreescribir: probablemente es un producto sin stock momentaneo
            # y Cero esta devolviendo 0 en lugar del precio real.
            preservar_p1 = (new_p1 == 0 and old_p1 > 0)
            preservar_p2 = (new_p2 == 0 and old_p2 > 0)
            if preservar_p1 or preservar_p2:
                skipped_zero.append(f"{codigo} ({(prod.get('name') or '')[:40]})")

            cambio_p1 = (new_p1 != old_p1) and not preservar_p1
            cambio_p2 = (new_p2 != old_p2) and not preservar_p2
            cambio_stock = (new_stock != old_stock)

            if cambio_p1 or cambio_p2 or cambio_stock:
                row = fila_info["row"]
                if cambio_p1:
                    updates.append({"range": f"{col_idx_to_letter(idx_p1)}{row}", "values": [[new_p1]]})
                if cambio_p2:
                    updates.append({"range": f"{col_idx_to_letter(idx_p2)}{row}", "values": [[new_p2]]})
                if cambio_stock:
                    updates.append({"range": f"{col_idx_to_letter(idx_stock)}{row}", "values": [[new_stock]]})
        else:
            # Nuevo producto. Si NO tiene precio mayorista (p1==0), no lo agregamos
            # para evitar ensuciar la hoja con productos sin precio.
            new_p1 = prod.get("salePrice") or 0
            if new_p1 == 0:
                skipped_new_zero += 1
                continue
            fila = producto_a_fila(prod, headers)
            nuevos_filas.append(fila)
            nuevos_meta.append({
                "sku": sku,
                "codigo": prod.get("supplierCode"),
                "nombre": prod.get("name") or prod.get("description") or "",
                "precio": new_p1,
            })

    if skipped_zero:
        log.warning(
            "Preservados %d precios historicos (la API devolvio $0, probable falta de stock).",
            len(skipped_zero),
        )
        for s in skipped_zero[:10]:
            log.warning("  preservado: %s", s)
        if len(skipped_zero) > 10:
            log.warning("  ...y %d mas", len(skipped_zero) - 10)
    if skipped_new_zero:
        log.warning("Saltados %d productos NUEVOS sin precio mayorista (no se agregan)", skipped_new_zero)

    log.info("Cambios a aplicar: %d updates, %d nuevos", len(updates), len(nuevos_filas))

    if dry_run:
        log.info("[DRY-RUN] No escribo en la hoja.")
        return len(updates), len(nuevos_filas), nuevos_meta

    # Escribir
    if updates:
        # gspread soporta batch_update con value_input_option
        ws.batch_update(updates, value_input_option="USER_ENTERED")
        log.info("Aplicados %d updates de precio/stock", len(updates))

    if nuevos_filas:
        ws.append_rows(nuevos_filas, value_input_option="USER_ENTERED")
        log.info("Agregadas %d filas nuevas al final", len(nuevos_filas))

    return len(updates), len(nuevos_filas), nuevos_meta


def _to_num(s: Any) -> float:
    """Convierte string tipo '41.177' (CL miles) o '41177' a float. Vacio -> 0."""
    if s is None or s == "":
        return 0.0
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip().replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


# ============================================================
# Main
# ============================================================
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="No escribe en la hoja, solo reporta")
    args = ap.parse_args()

    user = os.environ.get("CERO_USER")
    pwd = os.environ.get("CERO_PASS")
    sheet_id = os.environ.get("CERO_SHEET_ID")
    tab = os.environ.get("CERO_SHEET_TAB")
    if not user or not pwd:
        sys.exit("ERROR: falta CERO_USER y/o CERO_PASS")
    if not sheet_id or not tab:
        sys.exit("ERROR: falta CERO_SHEET_ID y/o CERO_SHEET_TAB")

    t0 = time.time()
    session = requests.Session()
    log.info("=== Cero spider iniciado ===")
    login_cero(session, user, pwd)

    productos = scrape_todo(session, (user, pwd))

    ws = conectar_sheet(sheet_id, tab)
    n_upd, n_new, nuevos = sincronizar(ws, productos, args.dry_run)

    duracion = round(time.time() - t0)
    resumen = (
        f"*Cero sync*  ✅\n"
        f"Productos scrapeados: {len(productos)}\n"
        f"Cambios de precio/stock: {n_upd}\n"
        f"Productos NUEVOS: {n_new}\n"
        f"Duracion: {duracion}s"
    )
    log.info(resumen.replace("*", ""))

    # Solo notifica si hay nuevos (lo que pidio el usuario)
    if n_new > 0 and not args.dry_run:
        # Detalle hasta los primeros 30 nuevos
        lineas = [f"🆕 *Importadora Cero: {n_new} productos nuevos*", ""]
        for n in nuevos[:30]:
            lineas.append(f"• `{n['codigo']}` — {n['nombre'][:60]}  ${int(n['precio']):,}".replace(",", "."))
        if n_new > 30:
            lineas.append(f"...y {n_new - 30} mas.")
        lineas.append("")
        lineas.append(f"Hoja: https://docs.google.com/spreadsheets/d/{sheet_id}")
        telegram_notify("\n".join(lineas))
    elif args.dry_run and n_new > 0:
        log.info("[DRY-RUN] Habria notificado %d nuevos por Telegram", n_new)

    return 0


if __name__ == "__main__":
    sys.exit(main())
