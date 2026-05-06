"""
cero_recover_formulas.py - Recupera las formulas calculadas en filas 137-189
columnas AJ:BQ que fueron sobrescritas con datos pegados por error.

Usa la API copyPaste de Google Sheets para clonar las formulas de la fila
100 hacia las filas 137-189, con ajuste de referencias relativo automatico.
"""
import os
import sys
import json
import logging

import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("recover")


def main() -> int:
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = os.environ.get("CERO_SHEET_ID")
    tab = os.environ.get("CERO_SHEET_TAB")
    if not (sa_json and sheet_id and tab):
        sys.exit("ERROR: faltan envs")

    creds = Credentials.from_service_account_info(
        json.loads(sa_json),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(tab)
    log.info("Conectado a '%s' / '%s' (sheet_id=%s)", sh.title, ws.title, ws.id)

    # Source: AJ100:BQ100  -> indices 0-based: row 99..100, col 35..69
    # Destination: AJ137:BQ189 -> indices 0-based: row 136..189, col 35..69
    SRC_ROW_START = 99    # row 100 (0-indexed)
    SRC_ROW_END   = 100   # exclusive -> row 100 only
    SRC_COL_START = 35    # AJ
    SRC_COL_END   = 69    # BQ exclusive -> AJ..BP. Pero queremos hasta BQ inclusive => 69
    # En Google Sheets API, endIndex es EXCLUSIVO. AJ=35, BQ=68. Para incluir BQ -> end=69.
    DST_ROW_START = 136   # row 137
    DST_ROW_END   = 189   # exclusive -> last row 189
    DST_COL_START = 35
    DST_COL_END   = 69

    log.info("Source: AJ100:BQ100 (1 fila)")
    log.info("Destino: AJ137:BQ189 (53 filas)")

    body = {
        "requests": [{
            "copyPaste": {
                "source": {
                    "sheetId": ws.id,
                    "startRowIndex": SRC_ROW_START,
                    "endRowIndex": SRC_ROW_END,
                    "startColumnIndex": SRC_COL_START,
                    "endColumnIndex": SRC_COL_END,
                },
                "destination": {
                    "sheetId": ws.id,
                    "startRowIndex": DST_ROW_START,
                    "endRowIndex": DST_ROW_END,
                    "startColumnIndex": DST_COL_START,
                    "endColumnIndex": DST_COL_END,
                },
                "pasteType": "PASTE_NORMAL",     # incluye formulas + formato
                "pasteOrientation": "NORMAL",
            }
        }]
    }
    resp = sh.batch_update(body)
    log.info("API response: %s", resp)
    log.info("Listo: las formulas en AJ-BQ desde la fila 137 a la 189 deberian estar restauradas.")
    log.info("Las referencias a L, M, etc se autoajustaron por fila (L100 -> L137, L138, ...).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
