"""
shopify_spider.py - Scraper generico para tiendas Shopify (usan /products.json).
Cubre: Repuestos Del Sol, MS Repuestos, OParts.
"""
import os
import sys
import datetime
import time
import requests
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    sys.exit("ERROR: faltan SUPABASE_URL y/o SUPABASE_(SERVICE_ROLE_)KEY en env.")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

SHOPIFY_SITES = [
    {"proveedor": "Repuestos Del Sol", "base_url": "https://www.repuestosdelsol.cl"},
    {"proveedor": "MS Repuestos",      "base_url": "https://www.msrepuestos.cl"},
    {"proveedor": "OParts",            "base_url": "https://oparts.cl"},
]

USER_AGENT = "Mozilla/5.0 (compatible; AVR-Bot/1.0)"


def scrape_shopify(proveedor, base_url):
    print(f"\n=== {proveedor} ({base_url}) ===")
    page = 1
    total = 0
    headers = {"User-Agent": USER_AGENT}
    while True:
        url = f"{base_url}/products.json?limit=250&page={page}"
        print(f"[{proveedor}] pag {page}")
        try:
            r = requests.get(url, headers=headers, timeout=20)
            if r.status_code != 200:
                print(f"  HTTP {r.status_code}, salgo")
                break
            data = r.json()
            products = data.get("products", [])
            if not products:
                break

            batch = []
            for p in products:
                nombre = p.get("title", "")
                handle = p.get("handle", "")
                product_url = f"{base_url}/products/{handle}"
                categoria = p.get("product_type") or "General"
                precio = 0.0
                variants = p.get("variants", [])
                if variants:
                    try:
                        precio = float(variants[0].get("price", "0") or "0")
                    except (TypeError, ValueError):
                        precio = 0.0
                imagen = ""
                images = p.get("images", [])
                if images:
                    imagen = images[0].get("src", "") or ""
                batch.append({
                    "proveedor": proveedor,
                    "categoria": str(categoria),
                    "url": str(product_url),
                    "nombre": str(nombre),
                    "precio": precio,
                    "imagen": str(imagen),
                    "fecha_actualizacion": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                })

            if batch:
                try:
                    supabase.table("productos_proveedores").upsert(batch, on_conflict="url").execute()
                    total += len(batch)
                except Exception as e:
                    print(f"  Supabase upsert fallo: {e}")
            page += 1
            time.sleep(0.5)
        except Exception as e:
            print(f"  Excepcion pag {page}: {e}")
            break
    print(f"[{proveedor}] {total} productos")


def main():
    for site in SHOPIFY_SITES:
        try:
            scrape_shopify(site["proveedor"], site["base_url"])
        except Exception as e:
            print(f"ERROR en {site['proveedor']}: {e}")


if __name__ == "__main__":
    main()
