# Bot Escreaper

Pipeline de web scraping para consolidar catálogos de repuestos automotrices chilenos en Supabase.

Mantenido por [AutovidriosRobin](https://autovidriosrobin.cl) (Robinson Sepúlveda) para uso interno del catálogo.

## Spiders

| Proveedor | Plataforma | Approach | Catálogo (aprox.) |
|---|---|---|---|
| `mundo` ([mundorepuestos.com](https://mundorepuestos.com)) | ASP.NET MVC | sitemap (`productos[1-3].xml`) + JSON-LD | ~57K productos |
| `adriazola` ([adriazolarepuestos.com](https://www.adriazolarepuestos.com)) | Bootstrap+jQuery | Crawl categorías + paginación `?desde=N` | ~6K productos (con stock por tienda) |
| `misleh` ([repuestosmisleh.cl](https://repuestosmisleh.cl)) | Custom + nginx | Crawl marca-cat + paginación `?page=N` | ~25K productos (sin precio, todo cotizable) |
| `ciper` ([ciper.cl](https://www.ciper.cl)) | VTEX | API JSON pública (`/api/catalog_system/pub/products/search`) | ~3K productos |
| `vigfor` ([vigfor.cl](https://vigfor.cl)) | SPA propietaria + Cloudflare | Playwright (CF resuelto, anti-bot fingerprint pendiente) | bloqueado por ahora |

Diagnóstico técnico de cada uno: ver docstring al inicio de cada `*_spider.py`.

## Tabla destino (Supabase)

`productos_proveedores` con columnas:

| Columna | Tipo | Notas |
|---|---|---|
| `id` | int (PK) | Autoincrement |
| `proveedor` | text | "Mundo Repuestos", "Adriazola Repuestos", etc. |
| `categoria` | text | Categoría/marca derivada |
| `url` | text (UNIQUE) | Conflict key del upsert |
| `nombre` | text | |
| `precio` | float | CLP. `0` si no se publica precio (misleh) |
| `imagen` | text | URL absoluta |
| `fecha_actualizacion` | timestamp UTC | ISO |

## Ejecución local

```bash
# Setup
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Variables obligatorias
export SUPABASE_URL=https://xxxxx.supabase.co
export SUPABASE_KEY=sb_publishable_xxxx

# Smoke test (5 productos)
python run_to_supabase.py adriazola --limit 5
python run_to_supabase.py ciper --limit 5

# Full run
python run_to_supabase.py mundo --workers 30
python run_to_supabase.py adriazola
python run_to_supabase.py misleh
python run_to_supabase.py ciper

# Auditoría
python supervisor.py
```

## GitHub Actions (cloud, gratis)

Workflows en `.github/workflows/`. Cron en UTC (Chile = UTC-4).

| Workflow | Cadencia | UTC | Chile |
|---|---|---|---|
| `supervisor.yml` | cada hora | `0 * * * *` | cada hora |
| `scrape-ciper.yml` | diario | `0 5 * * *` | 1am |
| `scrape-adriazola.yml` | lunes | `0 6 * * 1` | 2am |
| `scrape-misleh.yml` | lunes | `0 7 * * 1` | 3am |
| `scrape-mundo.yml` | lunes | `0 8 * * 1` | 4am |
| `scrape-vigfor.yml` | manual (dispatch) | — | desactivado por anti-bot |

**Secrets requeridos** (en repo Settings > Secrets and variables > Actions):
- `SUPABASE_URL`
- `SUPABASE_KEY`
- `TELEGRAM_BOT_TOKEN` (opcional, para alertas del supervisor)
- `TELEGRAM_CHAT_ID` (opcional)

Disparar un run manual: ir a la pestaña Actions del repo → workflow → "Run workflow".

## Supervisor

Lee la BD y reporta por proveedor: filas, última actualización, estado (ok / retraso 24h+ / caído 48h+ / nunca-ejecuto).
Si `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` están configurados, manda alertas Telegram cuando algún scraper falla.
