"""
masrepuestos_spider.py - Scraper de Mas Repuestos (PrestaShop).
Solo recorre la categoria "Espejos" como en la version original.
"""
import os
import sys
import datetime
import time
import requests
from bs4 import BeautifulSoup
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    sys.exit("ERROR: faltan SUPABASE_URL y/o SUPABASE_(SERVICE_ROLE_)KEY en env.")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

PROVEEDOR = "Más Repuestos"
USER_AGENT = "Mozilla/5.0 (compatible; AVR-Bot/1.0)"

CATEGORIAS = [
    ("Espejos", "https://www.masrepuestos.cl/espejo-repuestos-autos-santiago-calidad-alternativos-61"),
]


def parse_price(price_str):
    digits = ''.join(c for c in price_str if c.isdigit())
    return float(digits) if digits else 0.0


def scrape_categoria(category_url, categoria_nombre):
    print(f"\n=== {PROVEEDOR}/{categoria_nombre} ===")
    page = 1
    total = 0
    headers = {"User-Agent": USER_AGENT}
    while True:
        url = f"{category_url}?page={page}"
        print(f"[{PROVEEDOR}] pag {page}")
        try:
            r = requests.get(url, headers=headers, timeout=20)
            if r.status_code != 200:
                print(f"  HTTP {r.status_code}, salgo")
                break
            soup = BeautifulSoup(r.text, 'html.parser')
            products = soup.find_all('article', class_='product-miniature')
            if not products:
                break

            batch = []
            for p in products:
                title_elem = p.find('h2', class_='product-title')
                if not title_elem:
                    continue
                nombre = title_elem.text.strip()
                a = title_elem.find('a')
                product_url = a.get('href') if a else ""
                price_elem = p.find('span', class_='product-price')
                precio = parse_price(price_elem.text.strip()) if price_elem else 0.0
                img_elem = p.find('img', class_='product-thumbnail-first')
                imagen = img_elem.get('src') if img_elem else ""
                if img_elem and img_elem.has_attr('data-full-size-image-url'):
                    imagen = img_elem['data-full-size-image-url']
                batch.append({
                    "proveedor": PROVEEDOR,
                    "categoria": categoria_nombre,
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

            next_page = soup.find('a', rel='next')
            if not next_page:
                break
            page += 1
            time.sleep(0.5)
        except Exception as e:
            print(f"  Excepcion pag {page}: {e}")
            break

    print(f"[{categoria_nombre}] {total} productos")


def main():
    for nombre, url in CATEGORIAS:
        try:
            scrape_categoria(url, nombre)
        except Exception as e:
            print(f"ERROR en {nombre}: {e}")


if __name__ == "__main__":
    main()
