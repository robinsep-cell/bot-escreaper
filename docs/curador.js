/* Curador AVR — bookmarklet
 *
 * Carga: javascript:(function(){var s=document.createElement('script');s.src='https://robinsep-cell.github.io/bot-escreaper/curador.js?v='+Date.now();s.async=true;document.body.appendChild(s);})();
 *
 * Funciona en:
 *   - aliexpress.com (cualquier subdominio)
 *   - ebay.* (cualquier TLD)
 *
 * Que hace:
 *   1) Extrae del DOM lo que ve el usuario en pantalla (titulo, imagen, precio, envio, variante)
 *   2) Abre un popup editable
 *   3) Calcula costo total + precio venta CLP con la formula del usuario
 *   4) POST a Supabase. Upsert por url_origen (refresh idempotente).
 */
(function () {
  'use strict';

  // ===== Config (cambia el repo si forkeas) =====
  var SUPABASE_URL = 'https://vlhoshlnkmsojeqejzwo.supabase.co';
  var SUPABASE_KEY = 'sb_publishable_OIkK1MFmOv3GYy9tsDbvUA_Nx5kW2Q6';
  var TABLE = 'productos_curados';
  var IVA_PCT = 19;

  // ===== Formula precio venta (igual que curador_config.py) =====
  function precioVenta(costoClp) {
    if (costoClp < 15000) return { pv: 45000, mult: 0 };
    var n = Math.floor((costoClp - 15000) / 5000);
    var mult = Math.max(2.10, 3.00 - 0.02 * n);
    return { pv: costoClp * mult - 1000, mult: mult };
  }

  function calcular(productoClp, envioClp) {
    var subtotal = (productoClp || 0) + (envioClp || 0);
    var iva = subtotal * (IVA_PCT / 100);
    var costo = subtotal + iva;
    var r = precioVenta(costo);
    return { iva: iva, costo: costo, pv: r.pv, mult: r.mult };
  }

  function fmtCLP(n) {
    return '$' + Math.round(n || 0).toLocaleString('es-CL', { maximumFractionDigits: 0 });
  }

  // ===== Detección plataforma =====
  function detectarFuente() {
    var host = location.hostname.toLowerCase();
    if (host.indexOf('aliexpress') !== -1) return 'aliexpress';
    if (/\bebay\./.test(host)) return 'ebay';
    return null;
  }

  function extraerProductId(fuente) {
    if (fuente === 'aliexpress') {
      var m = location.pathname.match(/\/item\/(\d+)/);
      return m ? m[1] : null;
    }
    if (fuente === 'ebay') {
      var m = location.pathname.match(/\/itm\/(?:[\w-]+\/)?(\d{9,15})/);
      return m ? m[1] : null;
    }
    return null;
  }

  // ===== Parsers de texto =====
  function parsePrecio(text) {
    if (!text) return null;
    var s = String(text).replace(/CLP|US\$|USD|\$|\s/g, '');
    var m = s.match(/[\d.,]+/);
    if (!m) return null;
    var raw = m[0];
    // En CLP usan punto como miles ("134.800"), en USD usan punto decimal ("9.99")
    // Heuristica: si hay coma -> coma es decimal (en europeo). Si solo punto y >= 4 digitos -> miles.
    var n;
    if (raw.indexOf(',') !== -1) {
      n = parseFloat(raw.replace(/\./g, '').replace(',', '.'));
    } else if (raw.indexOf('.') !== -1 && raw.replace(/\./g, '').length >= 4) {
      n = parseInt(raw.replace(/\./g, ''), 10);
    } else {
      n = parseFloat(raw);
    }
    return isNaN(n) ? null : n;
  }

  // ===== Extraccion AliExpress =====
  function extraerAliexpress() {
    var data = { titulo: '', imagen: '', precio: null, envio: null, variante: '' };

    // Título
    var tituloSels = [
      'h1[data-pl="product-title"]',
      '.product-title-text',
      'h1.title--wrap--UUHae_g',
      '.product-name h1',
      'h1'
    ];
    for (var i = 0; i < tituloSels.length; i++) {
      var el = document.querySelector(tituloSels[i]);
      if (el && el.innerText && el.innerText.trim().length > 5) {
        data.titulo = el.innerText.trim().slice(0, 250);
        break;
      }
    }

    // Imagen
    var imgSels = [
      '.image-view-magnifier-wrap img',
      '.magnifier-image',
      '.image-view-image img',
      'img[src*="alicdn"]'
    ];
    for (var i = 0; i < imgSels.length; i++) {
      var el = document.querySelector(imgSels[i]);
      if (el && el.src) { data.imagen = el.src; break; }
    }

    // Precio: buscamos elementos visibles con un precio
    var priceSels = [
      '[class*="product-price-current"]',
      '[class*="price--currentPrice"]',
      '[class*="es--wrap"][class*="price"]',
      '.uniform-banner-box-price',
      '.product-price-value'
    ];
    for (var i = 0; i < priceSels.length; i++) {
      var el = document.querySelector(priceSels[i]);
      if (el) {
        var p = parsePrecio(el.innerText);
        if (p && p > 0) { data.precio = p; break; }
      }
    }

    // Envio: leer texto del body que contenga env[ií]o + numero/gratis
    var bodyText = document.body.innerText || '';
    var envioRegex = /(?:[Ee]nv[ií]o|[Ss]hipping|Shipping cost|Shipping fee|Despacho)\s*(?:a|to|para)?\s*[\w\s]*?[:.]?\s*(gratis|free|\$\s*[\d.,]+|[\d.,]+\s*CLP)/i;
    var envioMatch = bodyText.match(envioRegex);
    if (envioMatch) {
      var raw = envioMatch[1];
      if (/gratis|free/i.test(raw)) {
        data.envio = 0;
      } else {
        data.envio = parsePrecio(raw);
      }
    }

    // Variante seleccionada
    var varSels = [
      '.sku-item-active',
      '[class*="sku-item-image-active"]',
      '[class*="sku-property-item-active"]',
      '[class*="sku-item--selected"]'
    ];
    for (var i = 0; i < varSels.length; i++) {
      var el = document.querySelector(varSels[i]);
      if (el) {
        data.variante = (el.title || el.alt || el.innerText || '').trim().slice(0, 120);
        if (data.variante) break;
      }
    }

    return data;
  }

  // ===== UI Popup =====
  function abrirPopup(fuente, productId) {
    var existing = document.getElementById('avr-curador-popup');
    if (existing) existing.remove();

    var datos = fuente === 'aliexpress' ? extraerAliexpress() : { titulo: document.title, imagen: '', precio: null, envio: null, variante: '' };

    var wrap = document.createElement('div');
    wrap.id = 'avr-curador-popup';
    wrap.style.cssText = [
      'position:fixed', 'top:24px', 'right:24px', 'z-index:2147483647',
      'background:#fff', 'border-radius:12px',
      'box-shadow:0 12px 40px rgba(0,0,0,.30)',
      'width:380px', 'padding:16px',
      'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif',
      'font-size:14px', 'color:#222', 'line-height:1.4'
    ].join(';');

    function escapeHtml(s) {
      return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
      });
    }

    wrap.innerHTML = [
      '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">',
      '  <strong style="font-size:16px;">🛒 Curador AVR</strong>',
      '  <button id="avr-cerrar" type="button" style="border:none;background:none;font-size:22px;cursor:pointer;line-height:1;">×</button>',
      '</div>',
      '<div style="margin-bottom:8px;">',
      '  <label style="display:block;color:#666;margin-bottom:2px;font-size:12px;">Producto</label>',
      '  <textarea id="avr-titulo" rows="2" style="width:100%;box-sizing:border-box;padding:6px;border:1px solid #ddd;border-radius:4px;font-size:13px;resize:vertical;font-family:inherit;">' + escapeHtml(datos.titulo) + '</textarea>',
      '</div>',
      '<div style="display:flex;gap:8px;margin-bottom:8px;">',
      '  <div style="flex:1;">',
      '    <label style="display:block;color:#666;margin-bottom:2px;font-size:12px;">Precio CLP *</label>',
      '    <input id="avr-precio" type="number" value="' + (datos.precio || '') + '" placeholder="134800" style="width:100%;box-sizing:border-box;padding:6px;border:1px solid #ddd;border-radius:4px;font-size:13px;font-family:inherit;"/>',
      '  </div>',
      '  <div style="flex:1;">',
      '    <label style="display:block;color:#666;margin-bottom:2px;font-size:12px;">Envío CLP</label>',
      '    <input id="avr-envio" type="number" value="' + (datos.envio !== null ? datos.envio : '') + '" placeholder="45300" style="width:100%;box-sizing:border-box;padding:6px;border:1px solid #ddd;border-radius:4px;font-size:13px;font-family:inherit;"/>',
      '  </div>',
      '</div>',
      '<div style="margin-bottom:8px;">',
      '  <label style="display:block;color:#666;margin-bottom:2px;font-size:12px;">Variante seleccionada</label>',
      '  <input id="avr-variante" type="text" value="' + escapeHtml(datos.variante) + '" placeholder="Espejo izquierdo eléctrico" style="width:100%;box-sizing:border-box;padding:6px;border:1px solid #ddd;border-radius:4px;font-size:13px;font-family:inherit;"/>',
      '</div>',
      '<div style="margin-bottom:12px;">',
      '  <label style="display:block;color:#666;margin-bottom:2px;font-size:12px;">Vehículos compatibles / notas</label>',
      '  <input id="avr-notas" type="text" placeholder="Toyota Corolla 2014-2018" style="width:100%;box-sizing:border-box;padding:6px;border:1px solid #ddd;border-radius:4px;font-size:13px;font-family:inherit;"/>',
      '</div>',
      '<div id="avr-calc" style="background:#f5f5f5;padding:8px;border-radius:6px;margin-bottom:12px;font-size:13px;line-height:1.6;">',
      '  <div>Costo total a tu bolsillo: <strong id="avr-costo">—</strong></div>',
      '  <div>Precio venta sugerido: <strong id="avr-pv" style="color:#0a8;">—</strong></div>',
      '  <div style="font-size:11px;color:#888;" id="avr-detalle">producto + envío + IVA 19%, multiplicador escalonado</div>',
      '</div>',
      '<div style="display:flex;gap:8px;">',
      '  <button id="avr-cancelar" type="button" style="flex:1;padding:10px;border:1px solid #ddd;background:#fff;border-radius:6px;cursor:pointer;font-family:inherit;">Cancelar</button>',
      '  <button id="avr-guardar" type="button" style="flex:2;padding:10px;border:none;background:#0a8;color:#fff;border-radius:6px;cursor:pointer;font-weight:bold;font-family:inherit;">💾 Guardar</button>',
      '</div>',
      '<div id="avr-status" style="margin-top:8px;font-size:12px;color:#666;min-height:18px;"></div>'
    ].join('');

    document.body.appendChild(wrap);

    function recalcular() {
      var p = parseInt(document.getElementById('avr-precio').value, 10) || 0;
      var e = parseInt(document.getElementById('avr-envio').value, 10) || 0;
      var costoEl = document.getElementById('avr-costo');
      var pvEl = document.getElementById('avr-pv');
      var detEl = document.getElementById('avr-detalle');
      if (p === 0) {
        costoEl.textContent = '—';
        pvEl.textContent = '—';
        detEl.textContent = 'producto + envío + IVA 19%, multiplicador escalonado';
        return;
      }
      var r = calcular(p, e);
      costoEl.textContent = fmtCLP(r.costo) + ' CLP';
      pvEl.textContent = fmtCLP(r.pv) + ' CLP';
      var ganancia = r.pv - r.costo;
      detEl.textContent = 'Mult ' + r.mult.toFixed(2) + ' · ganancia ' + fmtCLP(ganancia);
    }

    document.getElementById('avr-precio').addEventListener('input', recalcular);
    document.getElementById('avr-envio').addEventListener('input', recalcular);
    recalcular();

    function cerrar() { wrap.remove(); }
    document.getElementById('avr-cerrar').onclick = cerrar;
    document.getElementById('avr-cancelar').onclick = cerrar;

    document.getElementById('avr-guardar').onclick = function () {
      var btn = document.getElementById('avr-guardar');
      var status = document.getElementById('avr-status');
      btn.disabled = true;
      btn.textContent = 'Guardando...';
      status.textContent = '';

      var titulo = document.getElementById('avr-titulo').value.trim();
      var precio = parseInt(document.getElementById('avr-precio').value, 10) || 0;
      var envio = parseInt(document.getElementById('avr-envio').value, 10) || 0;
      var variante = document.getElementById('avr-variante').value.trim();
      var notas = document.getElementById('avr-notas').value.trim();

      if (!titulo) {
        status.textContent = '⚠️ Falta título';
        btn.disabled = false;
        btn.textContent = '💾 Guardar';
        return;
      }
      if (!precio) {
        status.textContent = '⚠️ Falta precio CLP';
        btn.disabled = false;
        btn.textContent = '💾 Guardar';
        return;
      }

      var r = calcular(precio, envio);

      var payload = {
        fuente: fuente,
        url_origen: location.href,
        product_id_origen: productId,
        titulo: titulo,
        imagen_url: datos.imagen || null,
        precio_origen_local: precio,
        moneda_local: 'CLP',
        envio_clp: envio,
        envio_usd: 0,
        impuesto_pct: IVA_PCT,
        costo_total_clp: r.costo,
        precio_venta_clp: r.pv,
        multiplicador_aplicado: r.mult,
        variante: variante || null,
        vehiculos_compatibles: notas || null,
        categoria: 'Curado',
        notas: notas || null,
        agregado_por: 'Robin (bookmarklet)',
        ultima_revision: new Date().toISOString()
      };

      fetch(SUPABASE_URL + '/rest/v1/' + TABLE + '?on_conflict=url_origen', {
        method: 'POST',
        headers: {
          'apikey': SUPABASE_KEY,
          'Authorization': 'Bearer ' + SUPABASE_KEY,
          'Content-Type': 'application/json',
          'Prefer': 'resolution=merge-duplicates,return=representation'
        },
        body: JSON.stringify([payload])
      }).then(function (resp) {
        if (!resp.ok) {
          return resp.text().then(function (t) { throw new Error('HTTP ' + resp.status + ': ' + t); });
        }
        return resp.json();
      }).then(function (result) {
        var id = (result && result[0] && result[0].id) || '?';
        status.innerHTML = '✅ Guardado id=<strong>' + id + '</strong> · Costo ' + fmtCLP(r.costo) + ' → PV <strong style="color:#0a8;">' + fmtCLP(r.pv) + '</strong>';
        btn.textContent = '✅ Guardado';
        setTimeout(cerrar, 4000);
      }).catch(function (err) {
        status.textContent = '❌ Error: ' + err.message;
        btn.disabled = false;
        btn.textContent = '🔁 Reintentar';
      });
    };
  }

  // ===== Main =====
  var fuente = detectarFuente();
  if (!fuente) {
    alert('Curador AVR: esta página no es de AliExpress ni eBay.\n\nNavega a la ficha del producto y volvé a hacer click en el bookmarklet.');
    return;
  }
  var productId = extraerProductId(fuente);
  if (!productId) {
    alert('Curador AVR: no pude extraer el ID del producto desde la URL.\n\n¿Estás en una página de FICHA (no de búsqueda ni de categoría)?');
    return;
  }
  abrirPopup(fuente, productId);
})();
