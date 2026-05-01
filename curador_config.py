"""
Configuracion compartida del modulo curador (capturador, vigilante, calculador).
"""
import json
import os
import sys
from typing import Optional

# Supabase
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TABLE_CURADOS = "productos_curados"
TABLE_HISTORIAL = "precio_historial"

# Google Sheets
SHEET_ID = os.environ.get("CURADOR_SHEET_ID")
SHEET_TAB_PEGAR = "pegar"
SHEET_TAB_LOG = "log"

# Service account JSON: o vine como var entera, o como path a archivo
GOOGLE_SA_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
GOOGLE_SA_FILE = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")

# Tax / FX defaults
IVA_PCT = float(os.environ.get("IVA_PCT", "19"))
DEFAULT_USD_CLP = float(os.environ.get("DEFAULT_USD_CLP", "950"))


def fail_if_missing(*keys: str) -> None:
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        sys.exit(f"ERROR: faltan env vars: {', '.join(missing)}")


def load_service_account_credentials():
    """Devuelve google.oauth2.service_account.Credentials con scopes Sheets+Drive."""
    from google.oauth2.service_account import Credentials
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    if GOOGLE_SA_JSON:
        info = json.loads(GOOGLE_SA_JSON)
        return Credentials.from_service_account_info(info, scopes=scopes)
    if GOOGLE_SA_FILE:
        return Credentials.from_service_account_file(GOOGLE_SA_FILE, scopes=scopes)
    sys.exit("ERROR: define GOOGLE_SERVICE_ACCOUNT_JSON o GOOGLE_SERVICE_ACCOUNT_FILE")


def get_sheet():
    """Devuelve el objeto Spreadsheet (gspread)."""
    import gspread
    if not SHEET_ID:
        sys.exit("ERROR: define CURADOR_SHEET_ID")
    creds = load_service_account_credentials()
    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_ID)


def get_supabase():
    """Cliente Supabase con credenciales de env."""
    from supabase import create_client
    if not SUPABASE_URL or not SUPABASE_KEY:
        sys.exit("ERROR: faltan SUPABASE_URL / SUPABASE_KEY")
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def get_usd_clp(default: Optional[float] = None) -> float:
    """Tipo de cambio USD->CLP. Intenta API del BCCh (mindicador.cl), fallback a env/default."""
    import requests
    try:
        r = requests.get("https://mindicador.cl/api/dolar", timeout=8)
        r.raise_for_status()
        data = r.json()
        return float(data["serie"][0]["valor"])
    except Exception:
        pass
    return default if default is not None else DEFAULT_USD_CLP


# ----- Formula de precio de venta (de la tabla del usuario) ------------------

PISO_FIJO_CLP = 45_000      # si costo < 15000, PV = 45000
COSTO_BREAKPOINT = 15_000
STEP_CLP = 5_000
MULT_INICIAL = 3.00
MULT_DECREMENTO = 0.02
MULT_MIN = 2.10
DESCUENTO_FIJO = 1_000


def precio_venta_clp(costo_clp: float) -> tuple[float, float]:
    """Aplica la formula del usuario. Devuelve (precio_venta, multiplicador_efectivo)."""
    if costo_clp < COSTO_BREAKPOINT:
        return float(PISO_FIJO_CLP), 0.0  # no se aplico multiplicador
    n = int((costo_clp - COSTO_BREAKPOINT) // STEP_CLP)
    mult = max(MULT_MIN, MULT_INICIAL - MULT_DECREMENTO * n)
    pv = costo_clp * mult - DESCUENTO_FIJO
    return float(pv), float(mult)
