// Curador AVR — service worker
// Refresca precios de productos curados desde Supabase, abriendo cada uno en
// una pestaña en una ventana minimizada de Chrome del usuario.

const SUPABASE_URL = 'https://vlhoshlnkmsojeqejzwo.supabase.co';
const SUPABASE_KEY = 'sb_publishable_OIkK1MFmOv3GYy9tsDbvUA_Nx5kW2Q6';
const TABLE_CURADOS = 'productos_curados';
const TABLE_HISTORIAL = 'precio_historial';
const ALARM_NAME = 'avr-refresh-alarm';

// Configuración
const PERIOD_HOURS = 12;            // refresca cada 12h (puedes cambiarlo)
const STALENESS_HOURS = 18;         // considera "stale" si no se actualizó hace +18h
const MAX_PER_RUN = 25;             // máximo de productos por ciclo (politeness)
const DELAY_BETWEEN_MS = 8000;      // 8s entre productos
const PAGE_RENDER_WAIT_MS = 3500;   // espera tras carga para que el SPA renderice
const TAB_LOAD_TIMEOUT_MS = 35000;  // timeout de carga de pestaña

// === Setup: crear alarma al instalar / actualizar ===
chrome.runtime.onInstalled.addListener(async () => {
  await chrome.alarms.create(ALARM_NAME, {
    delayInMinutes: 2,
    periodInMinutes: PERIOD_HOURS * 60
  });
  console.log('[AVR] Extension instalada. Alarma cada', PERIOD_HOURS, 'horas');
  await chrome.storage.local.set({
    status: { currentlyRunning: false, lastRun: null, message: 'Recién instalado, primera corrida en 2 min' }
  });
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM_NAME) {
    console.log('[AVR] Alarma disparada');
    runRefreshCycle();
  }
});

// Permite que el popup pida correr ahora o leer estado
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === 'refresh-now') {
    runRefreshCycle();
    sendResponse({ ok: true });
    return false;
  }
  if (msg.action === 'get-status') {
    chrome.storage.local.get(['status'], (data) => {
      sendResponse(data.status || { currentlyRunning: false });
    });
    return true; // async response
  }
});

// === Ciclo de refresh ===
async function runRefreshCycle() {
  const { status } = await chrome.storage.local.get(['status']);
  if (status && status.currentlyRunning) {
    console.log('[AVR] Ya hay un ciclo corriendo, salgo');
    return;
  }
  await setStatus({ currentlyRunning: true, lastStarted: new Date().toISOString(), message: 'Buscando productos a refrescar...' });

  let products = [];
  try {
    products = await fetchProductsToRefresh();
  } catch (e) {
    console.error('[AVR] Error fetch productos:', e);
    await setStatus({ currentlyRunning: false, lastError: e.message, message: 'Error consultando Supabase' });
    return;
  }

  console.log(`[AVR] ${products.length} productos a refrescar`);
  if (products.length === 0) {
    await setStatus({ currentlyRunning: false, lastRun: new Date().toISOString(), lastUpdated: 0, lastFailed: 0, lastTotal: 0, message: 'Nada que refrescar (todos al día)' });
    return;
  }

  let updated = 0, failed = 0;
  let windowId = null;

  for (let i = 0; i < products.length; i++) {
    const p = products[i];
    await setStatus({ currentlyRunning: true, message: `Refrescando ${i + 1}/${products.length}: ${p.titulo ? p.titulo.slice(0, 50) : p.product_id_origen}` });
    try {
      windowId = await ensureRefreshWindow(windowId);
      await refreshOne(p, windowId);
      updated++;
    } catch (e) {
      console.error('[AVR] fallo id=', p.id, e.message);
      failed++;
    }
    if (i < products.length - 1) await sleep(DELAY_BETWEEN_MS);
  }

  // Cerrar la ventana minimizada cuando termine
  if (windowId !== null) {
    try { await chrome.windows.remove(windowId); } catch (_) {}
  }
  await chrome.storage.local.remove(['refreshWindowId']);

  await setStatus({
    currentlyRunning: false,
    lastRun: new Date().toISOString(),
    lastUpdated: updated,
    lastFailed: failed,
    lastTotal: products.length,
    message: `Listo: ${updated} actualizados, ${failed} fallidos`
  });
  console.log(`[AVR] Ciclo done. updated=${updated} failed=${failed}`);
}

async function fetchProductsToRefresh() {
  const cutoff = new Date(Date.now() - STALENESS_HOURS * 3600 * 1000).toISOString();
  // PostgREST: queremos los aliexpress activos cuya última_revision es vieja o nula
  const url = `${SUPABASE_URL}/rest/v1/${TABLE_CURADOS}?select=id,url_origen,product_id_origen,titulo,fuente,precio_venta_clp&fuente=eq.aliexpress&activo=eq.true&or=(ultima_revision.lt.${encodeURIComponent(cutoff)},ultima_revision.is.null)&order=ultima_revision.asc.nullsfirst&limit=${MAX_PER_RUN}`;
  const r = await fetch(url, {
    headers: { 'apikey': SUPABASE_KEY, 'Authorization': `Bearer ${SUPABASE_KEY}` }
  });
  if (!r.ok) throw new Error(`Supabase fetch ${r.status}: ${await r.text()}`);
  return await r.json();
}

async function ensureRefreshWindow(currentId) {
  if (currentId !== null) {
    try { const w = await chrome.windows.get(currentId); if (w) return currentId; } catch (_) {}
  }
  const w = await chrome.windows.create({
    url: 'about:blank',
    state: 'minimized',
    focused: false,
    type: 'normal'
  });
  await chrome.storage.local.set({ refreshWindowId: w.id });
  return w.id;
}

async function refreshOne(product, windowId) {
  const tab = await chrome.tabs.create({
    windowId: windowId,
    url: product.url_origen,
    active: false
  });

  try {
    await waitForTabComplete(tab.id, TAB_LOAD_TIMEOUT_MS);
    await sleep(PAGE_RENDER_WAIT_MS);

    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: extractAliexpress
    });
    const data = results && results[0] && results[0].result;
    if (!data || !data.precio || data.precio < 100) {
      throw new Error('Sin precio extraíble (' + JSON.stringify(data) + ')');
    }

    // Calcular costo + PV
    const subtotal = (data.precio || 0) + (data.envio || 0);
    const iva = subtotal * 0.19;
    const costo = subtotal + iva;
    const pv = computePV(costo);

    // PATCH update
    const upUrl = `${SUPABASE_URL}/rest/v1/${TABLE_CURADOS}?id=eq.${product.id}`;
    const upResp = await fetch(upUrl, {
      method: 'PATCH',
      headers: {
        'apikey': SUPABASE_KEY,
        'Authorization': `Bearer ${SUPABASE_KEY}`,
        'Content-Type': 'application/json',
        'Prefer': 'return=minimal'
      },
      body: JSON.stringify({
        precio_origen_local: data.precio,
        moneda_local: 'CLP',
        envio_clp: data.envio || 0,
        costo_total_clp: costo,
        precio_venta_clp: pv.pv,
        multiplicador_aplicado: pv.mult,
        ultima_revision: new Date().toISOString()
      })
    });
    if (!upResp.ok) throw new Error('Update ' + upResp.status + ': ' + await upResp.text());

    // Insert historial
    const histResp = await fetch(`${SUPABASE_URL}/rest/v1/${TABLE_HISTORIAL}`, {
      method: 'POST',
      headers: {
        'apikey': SUPABASE_KEY,
        'Authorization': `Bearer ${SUPABASE_KEY}`,
        'Content-Type': 'application/json',
        'Prefer': 'return=minimal'
      },
      body: JSON.stringify({
        producto_id: product.id,
        precio_origen_usd: null,
        envio_usd: null,
        costo_total_clp: costo,
        precio_venta_clp: pv.pv
      })
    });
    if (!histResp.ok) console.warn('[AVR] historial fail (no critico):', histResp.status);

    console.log('[AVR] OK id=', product.id, 'precio=', data.precio, 'envio=', data.envio, 'PV=', Math.round(pv.pv));
  } finally {
    try { await chrome.tabs.remove(tab.id); } catch (_) {}
  }
}

function waitForTabComplete(tabId, timeoutMs) {
  return new Promise((resolve, reject) => {
    let resolved = false;
    const timer = setTimeout(() => {
      if (!resolved) { resolved = true; chrome.tabs.onUpdated.removeListener(listener); reject(new Error('Tab load timeout')); }
    }, timeoutMs);
    const listener = (id, info) => {
      if (id === tabId && info.status === 'complete' && !resolved) {
        resolved = true;
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    };
    chrome.tabs.onUpdated.addListener(listener);
  });
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function setStatus(patch) {
  const { status } = await chrome.storage.local.get(['status']);
  await chrome.storage.local.set({ status: { ...(status || {}), ...patch } });
}

function computePV(costoClp) {
  if (costoClp < 15000) return { pv: 45000, mult: 0 };
  const n = Math.floor((costoClp - 15000) / 5000);
  const mult = Math.max(2.10, 3.00 - 0.02 * n);
  return { pv: costoClp * mult - 1000, mult };
}

// === Función inyectada en pestañas de AliExpress (replicada del bookmarklet) ===
function extractAliexpress() {
  function parsePrecio(text) {
    if (!text) return null;
    var s = String(text).replace(/CLP|US\$|USD|\$|\s/g, '');
    var m = s.match(/[\d.,]+/);
    if (!m) return null;
    var raw = m[0];
    var n;
    if (raw.indexOf(',') !== -1) n = parseFloat(raw.replace(/\./g, '').replace(',', '.'));
    else if (raw.indexOf('.') !== -1 && raw.replace(/\./g, '').length >= 4) n = parseInt(raw.replace(/\./g, ''), 10);
    else n = parseFloat(raw);
    return isNaN(n) ? null : n;
  }
  var data = { precio: null, envio: null };
  var priceSels = [
    '[class*="price-default--current"]',
    '[class*="product-price-current"]',
    '[class*="price--currentPrice"]',
    '[class*="currentPriceText"]'
  ];
  for (var i = 0; i < priceSels.length; i++) {
    var el = document.querySelector(priceSels[i]);
    if (el) { var p = parsePrecio(el.innerText); if (p && p > 0) { data.precio = p; break; } }
  }
  var allStrong = document.querySelectorAll('strong');
  for (var i = 0; i < allStrong.length; i++) {
    var t = (allStrong[i].innerText || '').trim();
    if (/^[Ee]nv[ií]o\s*:/.test(t)) {
      if (/gratis|free/i.test(t)) { data.envio = 0; break; }
      var m = t.match(/\$\s*[\d.,]+/);
      if (m) { var p = parsePrecio(m[0]); if (p !== null) { data.envio = p; break; } }
    }
  }
  return data;
}
