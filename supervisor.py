"""
Robot supervisor: audita la salud de los scrapers leyendo `productos_proveedores`
en Supabase. Por cada proveedor calcula:
- conteo total de filas
- timestamp de la ultima actualizacion
- horas desde la ultima actualizacion
- estado: ok | retraso | caido | nunca-ejecuto

Reporta a stdout, escribe un log en logs/supervisor.log y opcionalmente
manda alertas a Telegram si TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID estan en env.

Uso:
    python supervisor.py            # auditoria one-shot
    python supervisor.py --watch 1h # ejecutar cada hora (Ctrl+C para parar)

Cron / launchd recomendado: ejecutar cada 1-2 horas.
"""
from __future__ import annotations

import argparse
import datetime
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

import requests
from supabase import create_client, Client

# Credenciales por env var. Prefiere service_role (bypass RLS) para que el supervisor
# pueda leer datos completos de todos los proveedores sin importar policies.
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")
TABLE = os.environ.get("SUPABASE_TABLE", "productos_proveedores")

if not SUPABASE_URL or not SUPABASE_KEY:
    sys.exit(
        "ERROR: faltan SUPABASE_URL y/o SUPABASE_KEY como variables de entorno.\n"
        "  Local: agregalas al EnvironmentVariables del launchd plist.\n"
        "  GitHub Actions: configurarlas en Settings > Secrets and variables > Actions."
    )

# Lista canonica de proveedores que deberian estar reportando.
# Mantener sincronizada con run_to_supabase.py
PROVEEDORES_ESPERADOS = [
    "Repuestos Boston",
    "Repuestos Del Sol",
    "MS Repuestos",
    "Más Repuestos",
    "Korea Auto Parts",
    "Repuesto Center",
    "Mundo Repuestos",
    "Adriazola Repuestos",
    "Repuestos Misleh",
    "CIPER",
    "Vigfor",
]

# Umbrales (en horas)
HORAS_RETRASO = 24
HORAS_CAIDO = 48

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


def _telegram_alert(text: str) -> None:
    """Manda una alerta a Telegram si las env vars estan presentes."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        logging.warning("Telegram fallo: %s", e)


def auditar(sb: Client) -> dict:
    """Devuelve un dict {proveedor: {count, ultima, horas, estado}}."""
    ahora = datetime.datetime.now(datetime.timezone.utc)
    resultado: dict[str, dict] = {}

    for prov in PROVEEDORES_ESPERADOS:
        try:
            count_q = (
                sb.table(TABLE).select("proveedor", count="exact").eq("proveedor", prov).limit(1).execute()
            )
            cnt = count_q.count or 0

            last_q = (
                sb.table(TABLE)
                .select("fecha_actualizacion")
                .eq("proveedor", prov)
                .order("fecha_actualizacion", desc=True)
                .limit(1)
                .execute()
            )
            if not last_q.data:
                resultado[prov] = {"count": 0, "ultima": None, "horas": None, "estado": "nunca-ejecuto"}
                continue

            ts = last_q.data[0]["fecha_actualizacion"].replace("Z", "+00:00")
            ultima = datetime.datetime.fromisoformat(ts)
            horas = (ahora - ultima).total_seconds() / 3600

            if horas <= HORAS_RETRASO:
                estado = "ok"
            elif horas <= HORAS_CAIDO:
                estado = "retraso"
            else:
                estado = "caido"

            resultado[prov] = {"count": cnt, "ultima": ultima.isoformat(), "horas": horas, "estado": estado}
        except Exception as e:
            resultado[prov] = {"count": None, "ultima": None, "horas": None, "estado": "error", "error": str(e)}

    return resultado


def render_reporte(reporte: dict) -> tuple[str, list[str]]:
    """Renderiza reporte legible y devuelve (texto, alertas)."""
    lineas = []
    alertas = []
    estados_icono = {"ok": "✅", "retraso": "⚠️", "caido": "❌", "nunca-ejecuto": "⚪", "error": "💥"}
    total = 0

    lineas.append("=" * 64)
    lineas.append(f"AUDITORIA DE ROBOTS — {datetime.datetime.now().isoformat(timespec='seconds')}")
    lineas.append("=" * 64)
    lineas.append(f"{'Proveedor':<22} {'Filas':>8} {'Última act.':>12} {'Estado':>14}")
    lineas.append("-" * 64)

    for prov, d in reporte.items():
        cnt = d["count"] or 0
        total += cnt
        horas = d["horas"]
        h_str = f"{horas:.1f}h" if horas is not None else "-"
        ico = estados_icono.get(d["estado"], "?")
        lineas.append(f"{prov:<22} {cnt:>8} {h_str:>12} {ico} {d['estado']:>10}")
        if d["estado"] in ("caido", "error"):
            alertas.append(f"{ico} *{prov}*: {d['estado']} ({h_str})")
        elif d["estado"] == "nunca-ejecuto":
            alertas.append(f"{ico} *{prov}*: nunca ejecutado")

    lineas.append("-" * 64)
    lineas.append(f"{'TOTAL':<22} {total:>8}")
    lineas.append("=" * 64)
    return "\n".join(lineas), alertas


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", type=str, default=None, help="Ejecuta en loop con intervalo (ej: 1h, 30m)")
    args = parser.parse_args()

    # Logging a archivo + stdout
    log_path = LOG_DIR / "supervisor.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_path), logging.StreamHandler(sys.stdout)],
    )

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    def _ciclo():
        rep = auditar(sb)
        texto, alertas = render_reporte(rep)
        print(texto)
        logging.info("Auditoria ejecutada. Alertas: %d", len(alertas))
        if alertas:
            mensaje = "🤖 *Reporte Supervisor de Scrapers*\n\n" + "\n".join(alertas)
            _telegram_alert(mensaje)

    if args.watch is None:
        _ciclo()
        return 0

    # parse interval
    s = args.watch.lower().strip()
    if s.endswith("h"):
        secs = int(float(s[:-1]) * 3600)
    elif s.endswith("m"):
        secs = int(float(s[:-1]) * 60)
    else:
        secs = int(s)

    print(f"Modo watch: cada {secs}s. Ctrl+C para salir.")
    while True:
        _ciclo()
        try:
            time.sleep(secs)
        except KeyboardInterrupt:
            print("\nSupervisor detenido.")
            return 0


if __name__ == "__main__":
    sys.exit(main())
