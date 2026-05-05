"""
boston_spider.py - Scraper de Repuestos Boston (Magento HTML).
Recorre categorias hardcoded y hace upsert a Supabase.
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

PROVEEDOR = "Repuestos Boston"

CATEGORIAS_PRINCIPALES = [
    ("Accesorios", "https://www.repuestosboston.cl/repuestos-boston/accesorios.html"),
    ("Carrocería", "https://www.repuestosboston.cl/repuestos-boston/carroceria.html"),
    ("Dirección", "https://www.repuestosboston.cl/repuestos-boston/direccion.html"),
    ("Distribución", "https://www.repuestosboston.cl/repuestos-boston/distribucion.html"),
    ("Eléctrico", "https://www.repuestosboston.cl/repuestos-boston/electrico.html"),
    ("Embrague", "https://www.repuestosboston.cl/repuestos-boston/embrague.html"),
    ("Filtros", "https://www.repuestosboston.cl/repuestos-boston/filtros.html"),
    ("Frenos", "https://www.repuestosboston.cl/repuestos-boston/frenos.html"),
    ("Iluminación", "https://www.repuestosboston.cl/repuestos-boston/iluminacion.html"),
    ("Lubricantes", "https://www.repuestosboston.cl/repuestos-boston/lubricantes.html"),
    ("Motor", "https://www.repuestosboston.cl/repuestos-boston/motor.html"),
    ("Piolas", "https://www.repuestosboston.cl/repuestos-boston/piolas.html"),
    ("Refrigeración", "https://www.repuestosboston.cl/repuestos-boston/refrigeracion.html"),
    ("Rodamientos y Poleas", "https://www.repuestosboston.cl/repuestos-boston/rodamientos-y-poleas.html"),
    ("Sensores", "https://www.repuestosboston.cl/repuestos-boston/sensores.html"),
    ("Soportes de Motor", "https://www.repuestosboston.cl/repuestos-boston/soportes-de-motor.html"),
    ("Suspensión", "https://www.repuestosboston.cl/repuestos-boston/suspension.html"),
    ("Turbos", "https://www.repuestosboston.cl/repuestos-boston/turbos.html"),
]

USER_AGENT = "Mozilla/5.0 (compatible; AVR-Bot/1.0)"


def parse_price(price_str):
    digits = ''.join(c for c in price_str if c.isdigit())
    return float(digits) if digits else 0.0


def scrape_categoria(start_url, categoria):
    headers = {"User-Agent": USER_AGENT}
    current_url = start_url
    page_count = 1
    total = 0
    while current_url:
        print(f"[{categoria}] pag {page_count}: {current_url}")
        try:
            r = requests.get(current_url, headers=headers, timeout=20)
            if r.status_code != 200:
                print(f"  HTTP {r.status_code}, salgo")
                break
        except Exception as e:
            print(f"  Excepcion: {e}")
            break

        soup = BeautifulSoup(r.text, 'html.parser')
        products = soup.find_all('li', class_='product-item')
        if not products:
            break

        batch = []
        for p in products:
            name_elem = p.find('a', class_='product-item-link')
            if not name_elem:
                continue
            nombre = name_elem.text.strip()
            url = name_elem.get('href', '')
            price_elem = p.find('span', class_='price')
            precio = parse_price(price_elem.text.strip()) if price_elem else 0.0
            img_elem = p.find('img', class_='product-image-photo')
            imagen = img_elem.get('src', '') if img_elem else ''
            if img_elem and img_elem.has_attr('data-src'):
                imagen = img_elem['data-src']
            batch.append({
                "proveedor": PROVEEDOR,
                "categoria": str(categoria),
                "url": str(url),
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

        next_page = soup.find('a', class_='action next')
        if next_page and next_page.get('href'):
            current_url = next_page.get('href')
            page_count += 1
            time.sleep(0.5)
        else:
            current_url = None

    print(f"[{categoria}] {total} productos")


def main():
    print(f"=== {PROVEEDOR} ===")
    grand_total = 0
    for nombre, url in CATEGORIAS_PRINCIPALES:
        try:
            scrape_categoria(url, nombre)
        except Exception as e:
            print(f"  ERROR en {nombre}: {e}")
    print(f"=== Fin {PROVEEDOR} ===")


if __name__ == "__main__":
    main()
