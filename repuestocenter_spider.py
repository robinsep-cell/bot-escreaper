"""
repuestocenter_spider.py - Scraper de Repuesto Center (WooCommerce).
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

PROVEEDOR = "Repuesto Center"
USER_AGENT = "Mozilla/5.0 (compatible; AVR-Bot/1.0)"


def parse_price(price_str):
    digits = ''.join(c for c in price_str if c.isdigit())
    return float(digits) if digits else 0.0


def scrape():
    print(f"\n=== {PROVEEDOR} ===")
    page = 1
    total = 0
    headers = {"User-Agent": USER_AGENT}
    while True:
        url = f"https://repuestocenter.cl/catalogo-en-linea/page/{page}/"
        print(f"[{PROVEEDOR}] pag {page}")
        try:
            r = requests.get(url, headers=headers, timeout=20)
            if r.status_code == 404:
                break
            if r.status_code != 200:
                print(f"  HTTP {r.status_code}, salgo")
                break
            soup = BeautifulSoup(r.text, 'html.parser')
            products = soup.find_all('div', class_='product')
            if not products:
                break

            batch = []
            for p in products:
                title_elem = p.find('h3', class_='wd-entities-title') \
                             or p.find('h2', class_='woocommerce-loop-product__title')
                if not title_elem:
                    continue
                nombre = title_elem.text.strip()
                a = title_elem.find('a')
                product_url = a.get('href') if a else (p.find('a').get('href') if p.find('a') else "")
                price_elem = p.find('span', class_='price')
                precio = parse_price(price_elem.text.strip()) if price_elem else 0.0
                img_elem = p.find('img', class_='attachment-woocommerce_thumbnail')
                imagen = ""
                if img_elem and img_elem.has_attr('data-srcset'):
                    imagen = img_elem.get('data-srcset', '').split(' ')[0]
                if not imagen and img_elem:
                    imagen = img_elem.get('src', '')
                classes = p.get('class', []) or []
                cats = [c.replace('product_cat-', '') for c in classes if c.startswith('product_cat-')]
                categoria = cats[0].replace('-', ' ').title() if cats else "Catálogo"
                batch.append({
                    "proveedor": PROVEEDOR,
                    "categoria": categoria,
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

    print(f"[{PROVEEDOR}] {total} productos")


if __name__ == "__main__":
    scrape()
