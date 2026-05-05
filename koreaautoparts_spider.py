"""
koreaautoparts_spider.py - Scraper de Korea Auto Parts.
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

PROVEEDOR = "Korea Auto Parts"
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
        url = f"https://www.koreaautoparts.cl/search?page={page}"
        print(f"[{PROVEEDOR}] pag {page}")
        try:
            r = requests.get(url, headers=headers, timeout=20)
            if r.status_code != 200:
                print(f"  HTTP {r.status_code}, salgo")
                break
            soup = BeautifulSoup(r.text, 'html.parser')
            products = soup.find_all('div', class_='product-block')
            if not products:
                break

            batch = []
            for p in products:
                anchor = p.find('a', class_='product-block__anchor')
                if not anchor:
                    continue
                nombre = (anchor.get('title', '') or '').replace('Ir a ', '')
                product_url = "https://www.koreaautoparts.cl" + anchor.get('href', '')
                img_elem = anchor.find('img', class_='product-block__image')
                imagen = img_elem.get('src', '') if img_elem else ""
                price_wrapper = p.find('div', class_='product-block__price')
                precio = 0.0
                if price_wrapper:
                    price_elem = price_wrapper.find('span', class_='product-block__price--new') \
                                 or price_wrapper.find('span')
                    precio = parse_price(price_elem.text.strip()) if price_elem else 0.0
                batch.append({
                    "proveedor": PROVEEDOR,
                    "categoria": "General",
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
