/* Curador AVR — bookmarklet (con login)
 *
 * Carga: javascript:(function(){var s=document.createElement('script');s.src='https://robinsep-cell.github.io/bot-escreaper/curador.js?v='+Date.now();s.async=true;document.body.appendChild(s);})();
 *
 * Funciona en:
 *   - aliexpress.com (cualquier subdominio)
 *   - ebay.* (cualquier TLD)
 *
 * Que hace:
 *   1) Pide login (email + password) si no hay sesion en localStorage
 *   2) Extrae del DOM lo que ve el usuario (titulo, imagen, precio, envio, variante)
 *   3) Abre un popup editable
 *   4) Calcula costo total + precio venta CLP con la formula del usuario
 *   5) POST a Supabase usando JWT (auth.uid() queda registrado como agregado_por_user_id)
 */
(function () {
  'use strict';

  // ===== Config =====
  var SUPABASE_URL = 'https://vlhoshlnkmsojeqejzwo.supabase.co';
  var SUPABASE_KEY = 'sb_publishable_OIkK1MFmOv3GYy9tsDbvUA_Nx5kW2Q6';
  var TABLE = 'productos_curados';
  var IVA_PCT = 19;
  var SESSION_KEY = 'avr_curador_session';

  // ===== Sesion / Auth =====
  function getSession() {
    try {
      var raw = localStorage.getItem(SESSION_KEY);
      if (!raw) return null;
      var s = JSON.parse(raw);
      if (!s || !s.access_token) return null;
      // expirado?
      if (s.expires_at && Date.now() / 1000 > s.expires_at - 30) return s; // dejamos el refresh para abajo
      return s;
    } catch (e) { return null; }
  }
  function saveSession(s) {
    try { localStorage.setItem(SESSION_KEY, JSON.stringify(s)); } catch (e) {}
  }
  function clearSession() {
    try { localStorage.removeItem(SESSION_KEY); } catch (e) {}
  }
  function isExpired(s) {
    if (!s || !s.expires_at) return true;
    return Date.now() / 1000 > s.expires_at - 30;
  }

  function loginPassword(email, password) {
    return fetch(SUPABASE_URL + '/auth/v1/token?grant_type=password', {
      method: 'POST',
      headers: { 'apikey': SUPABASE_KEY, 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email, password: password })
    }).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) throw new Error(j.error_description || j.msg || j.error || ('HTTP ' + r.status));
        return j;
      });
    });
  }
  // Pide a Supabase que mande un codigo OTP de 6 digitos al email del usuario.
  function pedirOTP(email) {
    return fetch(SUPABASE_URL + '/auth/v1/otp', {
      method: 'POST',
      headers: { 'apikey': SUPABASE_KEY, 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email, create_user: false })
    }).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) throw new Error(j.error_description || j.msg || j.error || ('HTTP ' + r.status));
        return j;
      });
    });
  }
  // Verifica el codigo OTP y devuelve la sesion.
  function verificarOTP(email, token) {
    return fetch(SUPABASE_URL + '/auth/v1/verify', {
      method: 'POST',
      headers: { 'apikey': SUPABASE_KEY, 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email, token: token, type: 'email' })
    }).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) throw new Error(j.error_description || j.msg || j.error || ('HTTP ' + r.status));
        return j;
      });
    });
  }
  function refreshToken(refresh_token) {
    return fetch(SUPABASE_URL + '/auth/v1/token?grant_type=refresh_token', {
      method: 'POST',
      headers: { 'apikey': SUPABASE_KEY, 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refresh_token })
    }).then(function (r) { if (!r.ok) throw new Error('refresh ' + r.status); return r.json(); });
  }
  // Devuelve session valida (refresca si toca). Si falla -> null
  function ensureSession() {
    var s = getSession();
    if (!s) return Promise.resolve(null);
    if (!isExpired(s)) return Promise.resolve(s);
    if (!s.refresh_token) return Promise.resolve(null);
    return refreshToken(s.refresh_token).then(function (n) {
      saveSession(n); return n;
    }).catch(function () { clearSession(); return null; });
  }

  // ===== Formula precio venta =====
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

  // ===== Deteccion plataforma =====
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
      var m2 = location.pathname.match(/\/itm\/(?:[\w-]+\/)?(\d{9,15})/);
      return m2 ? m2[1] : null;
    }
    return null;
  }

  // ===== Parsers =====
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

  function extraerAliexpress() {
    var data = { titulo: '', imagen: '', precio: null, envio: null, variante: '' };
    var allH1 = document.querySelectorAll('h1');
    var bestH1 = null, bestLen = 0;
    for (var i = 0; i < allH1.length; i++) {
      var t = (allH1[i].innerText || '').trim();
      if (t.length > bestLen) { bestLen = t.length; bestH1 = allH1[i]; }
    }
    if (bestH1) data.titulo = bestH1.innerText.trim().slice(0, 250);

    var imgSels = ['.image-view-magnifier-wrap img','.magnifier-image','.image-view-image img','[class*="slider--img"][class*="active"] img','img[src*="alicdn"]'];
    for (var j = 0; j < imgSels.length; j++) { var elI = document.querySelector(imgSels[j]); if (elI && elI.src) { data.imagen = elI.src; break; } }

    var priceSels = ['[class*="price-default--current"]','[class*="product-price-current"]','[class*="price--currentPrice"]','[class*="currentPriceText"]','.uniform-banner-box-price','.product-price-value'];
    for (var k = 0; k < priceSels.length; k++) {
      var elP = document.querySelector(priceSels[k]);
      if (elP) { var p = parsePrecio(elP.innerText); if (p && p > 0) { data.precio = p; break; } }
    }

    var allStrong = document.querySelectorAll('strong');
    for (var l = 0; l < allStrong.length; l++) {
      var ts = (allStrong[l].innerText || '').trim();
      if (/^[Ee]nv[ií]o\s*:/.test(ts)) {
        if (/gratis|free/i.test(ts)) { data.envio = 0; break; }
        var mm = ts.match(/\$\s*[\d.,]+/);
        if (mm) { var pp = parsePrecio(mm[0]); if (pp !== null) { data.envio = pp; break; } }
      }
    }
    if (data.envio === null) {
      var bodyText = document.body.innerText || '';
      var em = bodyText.match(/(?:[Ee]nv[ií]o|[Ss]hipping|Despacho)[^\n]{0,80}?(gratis|free|\$\s*[\d.,]+|[\d.,]+\s*CLP)/i);
      if (em) {
        var raw = em[1];
        if (/gratis|free/i.test(raw)) data.envio = 0; else data.envio = parsePrecio(raw);
      }
    }

    var varSels = ['[class*="sku-item--selected"][class*="sku-item--image"]','[class*="sku-item--selected"]','[class*="sku-property-item-active"]','[class*="sku-item-image-active"]','.sku-item-active'];
    for (var v = 0; v < varSels.length; v++) {
      var elV = document.querySelector(varSels[v]);
      if (elV) {
        var img = elV.querySelector('img');
        var vv = (img && img.alt) || (img && img.title) || elV.title || elV.getAttribute('aria-label') || elV.innerText || elV.getAttribute('data-sku-col') || '';
        vv = vv.replace(/\s+/g, ' ').trim();
        if (vv) { data.variante = vv.slice(0, 120); break; }
      }
    }
    if (!data.variante) {
      var labels = document.querySelectorAll('div, span, p');
      for (var w = 0; w < labels.length; w++) {
        var txt = (labels[w].innerText || '').trim();
        var m3 = txt.match(/(?:[Cc]olor|[Vv]ariante|[Mm]odelo|[Tt]ipo)[^:]*:\s*(.+?)$/);
        if (m3 && m3[1] && m3[1].length < 120 && labels[w].children.length < 5) { data.variante = m3[1].trim(); break; }
      }
    }
    return data;
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // ===== UI: Login popup =====
  function abrirLogin(onSuccess) {
    var existing = document.getElementById('avr-curador-popup');
    if (existing) existing.remove();

    var wrap = document.createElement('div');
    wrap.id = 'avr-curador-popup';
    wrap.style.cssText = ['position:fixed','top:24px','right:24px','z-index:2147483647','background:#fff','border-radius:12px','box-shadow:0 12px 40px rgba(0,0,0,.30)','width:360px','padding:18px','font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif','font-size:14px','color:#222','line-height:1.4'].join(';');
    wrap.innerHTML = [
      '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">',
      '  <strong style="font-size:16px;">🔐 Curador AVR — Login</strong>',
      '  <button id="avr-cerrar-login" type="button" style="border:none;background:none;font-size:22px;cursor:pointer;line-height:1;">×</button>',
      '</div>',
      '<p style="margin:0 0 12px;font-size:12px;color:#666;">Ingresa con tu cuenta de AVR. La sesión queda guardada en este navegador.</p>',
      '<div style="display:flex;background:#f0f0f0;border-radius:6px;padding:3px;margin-bottom:12px;">',
      '  <button id="avr-tab-pass" type="button" style="flex:1;padding:7px;border:none;background:#fff;color:#1560a8;border-radius:4px;font-weight:600;cursor:pointer;font-family:inherit;font-size:13px;box-shadow:0 1px 2px rgba(0,0,0,.05);">Contraseña</button>',
      '  <button id="avr-tab-otp" type="button" style="flex:1;padding:7px;border:none;background:none;color:#666;border-radius:4px;cursor:pointer;font-family:inherit;font-size:13px;">Código por email</button>',
      '</div>',
      '<label style="display:block;margin-bottom:8px;">',
      '  <span style="display:block;font-size:12px;font-weight:600;color:#444;margin-bottom:3px;">Email</span>',
      '  <input id="avr-login-email" type="email" autocomplete="username" style="width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid #ddd;border-radius:6px;font-size:13px;font-family:inherit;"/>',
      '</label>',
      '<div id="avr-pass-block">',
      '  <label style="display:block;margin-bottom:12px;">',
      '    <span style="display:block;font-size:12px;font-weight:600;color:#444;margin-bottom:3px;">Contraseña</span>',
      '    <input id="avr-login-pass" type="password" autocomplete="current-password" style="width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid #ddd;border-radius:6px;font-size:13px;font-family:inherit;"/>',
      '  </label>',
      '  <button id="avr-login-btn" type="button" style="width:100%;padding:11px;border:none;background:#1560a8;color:#fff;border-radius:6px;cursor:pointer;font-weight:bold;font-size:14px;font-family:inherit;">Entrar</button>',
      '</div>',
      '<div id="avr-otp-block" style="display:none;">',
      '  <button id="avr-otp-send" type="button" style="width:100%;padding:11px;border:none;background:#0a8;color:#fff;border-radius:6px;cursor:pointer;font-weight:bold;font-size:14px;font-family:inherit;margin-bottom:10px;">📩 Enviarme código al email</button>',
      '  <label id="avr-otp-label" style="display:none;margin-bottom:12px;">',
      '    <span style="display:block;font-size:12px;font-weight:600;color:#444;margin-bottom:3px;">Código de 6 dígitos</span>',
      '    <input id="avr-login-otp" type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="123456" style="width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid #ddd;border-radius:6px;font-size:18px;font-family:monospace;letter-spacing:6px;text-align:center;"/>',
      '  </label>',
      '  <button id="avr-otp-verify" type="button" style="display:none;width:100%;padding:11px;border:none;background:#1560a8;color:#fff;border-radius:6px;cursor:pointer;font-weight:bold;font-size:14px;font-family:inherit;">Verificar código</button>',
      '</div>',
      '<div id="avr-login-status" style="margin-top:8px;font-size:12px;min-height:18px;"></div>'
    ].join('');
    document.body.appendChild(wrap);

    document.getElementById('avr-cerrar-login').onclick = function () { wrap.remove(); };
    var emailEl = document.getElementById('avr-login-email');
    var passEl  = document.getElementById('avr-login-pass');
    var btn     = document.getElementById('avr-login-btn');
    var status  = document.getElementById('avr-login-status');
    var tabPass = document.getElementById('avr-tab-pass');
    var tabOtp  = document.getElementById('avr-tab-otp');
    var blkPass = document.getElementById('avr-pass-block');
    var blkOtp  = document.getElementById('avr-otp-block');
    var btnSend = document.getElementById('avr-otp-send');
    var lblOtp  = document.getElementById('avr-otp-label');
    var inpOtp  = document.getElementById('avr-login-otp');
    var btnVer  = document.getElementById('avr-otp-verify');

    function setTab(modo) {
      status.textContent = ''; status.style.color = '#c00';
      if (modo === 'pass') {
        tabPass.style.background = '#fff'; tabPass.style.color = '#1560a8'; tabPass.style.boxShadow = '0 1px 2px rgba(0,0,0,.05)';
        tabOtp.style.background = 'transparent'; tabOtp.style.color = '#666'; tabOtp.style.boxShadow = 'none';
        blkPass.style.display = ''; blkOtp.style.display = 'none';
      } else {
        tabOtp.style.background = '#fff'; tabOtp.style.color = '#1560a8'; tabOtp.style.boxShadow = '0 1px 2px rgba(0,0,0,.05)';
        tabPass.style.background = 'transparent'; tabPass.style.color = '#666'; tabPass.style.boxShadow = 'none';
        blkPass.style.display = 'none'; blkOtp.style.display = '';
      }
    }
    tabPass.onclick = function () { setTab('pass'); };
    tabOtp.onclick  = function () { setTab('otp'); };

    // Login con contraseña
    function intentarPass() {
      var em = (emailEl.value || '').trim();
      var pw = passEl.value || '';
      if (!em || !pw) { status.style.color = '#c00'; status.textContent = 'Completa email y contraseña.'; return; }
      btn.disabled = true; btn.textContent = 'Validando…'; status.textContent = '';
      loginPassword(em, pw).then(function (sess) {
        saveSession(sess); wrap.remove(); onSuccess(sess);
      }).catch(function (err) {
        btn.disabled = false; btn.textContent = 'Entrar';
        status.style.color = '#c00';
        status.textContent = '❌ ' + (err.message || 'Login fallido');
      });
    }
    btn.onclick = intentarPass;
    passEl.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') intentarPass(); });

    // Login con OTP
    btnSend.onclick = function () {
      var em = (emailEl.value || '').trim();
      if (!em) { status.style.color = '#c00'; status.textContent = 'Pon tu email arriba.'; return; }
      btnSend.disabled = true; btnSend.textContent = 'Enviando…'; status.textContent = '';
      pedirOTP(em).then(function () {
        btnSend.disabled = false; btnSend.textContent = '📩 Reenviar código';
        lblOtp.style.display = ''; btnVer.style.display = '';
        status.style.color = '#0a8';
        status.textContent = '✅ Código enviado a ' + em + '. Revisa tu bandeja (puede tardar 1 min).';
        setTimeout(function () { inpOtp.focus(); }, 50);
      }).catch(function (err) {
        btnSend.disabled = false; btnSend.textContent = '📩 Enviarme código al email';
        status.style.color = '#c00';
        status.textContent = '❌ ' + err.message;
      });
    };
    function intentarOTP() {
      var em = (emailEl.value || '').trim();
      var code = (inpOtp.value || '').trim();
      if (!em || !code) { status.style.color = '#c00'; status.textContent = 'Falta email o código.'; return; }
      btnVer.disabled = true; btnVer.textContent = 'Verificando…'; status.textContent = '';
      verificarOTP(em, code).then(function (sess) {
        saveSession(sess); wrap.remove(); onSuccess(sess);
      }).catch(function (err) {
        btnVer.disabled = false; btnVer.textContent = 'Verificar código';
        status.style.color = '#c00';
        status.textContent = '❌ ' + err.message;
      });
    }
    btnVer.onclick = intentarOTP;
    inpOtp.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') intentarOTP(); });

    setTimeout(function () { emailEl.focus(); }, 50);
  }

  // ===== UI: Curador popup =====
  function abrirPopup(fuente, productId, sess) {
    var existing = document.getElementById('avr-curador-popup');
    if (existing) existing.remove();

    var datos = fuente === 'aliexpress' ? extraerAliexpress() : { titulo: document.title, imagen: '', precio: null, envio: null, variante: '' };
    var userEmail = (sess && sess.user && sess.user.email) || '';

    var wrap = document.createElement('div');
    wrap.id = 'avr-curador-popup';
    wrap.style.cssText = ['position:fixed','top:24px','right:24px','z-index:2147483647','background:#fff','border-radius:12px','box-shadow:0 12px 40px rgba(0,0,0,.30)','width:380px','padding:16px','font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif','font-size:14px','color:#222','line-height:1.4'].join(';');

    wrap.innerHTML = [
      '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">',
      '  <strong style="font-size:16px;">🛒 Curador AVR</strong>',
      '  <button id="avr-cerrar" type="button" style="border:none;background:none;font-size:22px;cursor:pointer;line-height:1;">×</button>',
      '</div>',
      '<div style="font-size:11px;color:#888;margin-bottom:10px;">Sesión: ' + escapeHtml(userEmail) + ' · <a href="#" id="avr-logout" style="color:#c00;text-decoration:none;">cerrar sesión</a></div>',
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
      if (p === 0) { costoEl.textContent = '—'; pvEl.textContent = '—'; detEl.textContent = 'producto + envío + IVA 19%, multiplicador escalonado'; return; }
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
    document.getElementById('avr-logout').onclick = function (ev) { ev.preventDefault(); clearSession(); cerrar(); abrirLogin(function (s) { abrirPopup(fuente, productId, s); }); };

    document.getElementById('avr-guardar').onclick = function () {
      var btn = document.getElementById('avr-guardar');
      var status = document.getElementById('avr-status');
      btn.disabled = true; btn.textContent = 'Guardando...'; status.textContent = '';

      var titulo = document.getElementById('avr-titulo').value.trim();
      var precio = parseInt(document.getElementById('avr-precio').value, 10) || 0;
      var envio = parseInt(document.getElementById('avr-envio').value, 10) || 0;
      var variante = document.getElementById('avr-variante').value.trim();
      var notas = document.getElementById('avr-notas').value.trim();

      if (!titulo) { status.textContent = '⚠️ Falta título'; btn.disabled = false; btn.textContent = '💾 Guardar'; return; }
      if (!precio) { status.textContent = '⚠️ Falta precio CLP'; btn.disabled = false; btn.textContent = '💾 Guardar'; return; }

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
        agregado_por: userEmail || 'bookmarklet',
        ultima_revision: new Date().toISOString()
      };

      ensureSession().then(function (validSess) {
        if (!validSess) {
          cerrar();
          abrirLogin(function (newS) { abrirPopup(fuente, productId, newS); });
          throw new Error('Sesión expirada, vuelve a entrar');
        }
        return fetch(SUPABASE_URL + '/rest/v1/' + TABLE + '?on_conflict=url_origen', {
          method: 'POST',
          headers: {
            'apikey': SUPABASE_KEY,
            'Authorization': 'Bearer ' + validSess.access_token,
            'Content-Type': 'application/json',
            'Prefer': 'resolution=merge-duplicates,return=representation'
          },
          body: JSON.stringify([payload])
        });
      }).then(function (resp) {
        if (!resp) return; // ya manejado
        if (!resp.ok) return resp.text().then(function (t) { throw new Error('HTTP ' + resp.status + ': ' + t); });
        return resp.json();
      }).then(function (result) {
        if (!result) return;
        var id = (result && result[0] && result[0].id) || '?';
        status.innerHTML = '✅ Guardado id=<strong>' + id + '</strong> · Costo ' + fmtCLP(r.costo) + ' → PV <strong style="color:#0a8;">' + fmtCLP(r.pv) + '</strong>';
        btn.textContent = '✅ Guardado';
        setTimeout(cerrar, 4000);
      }).catch(function (err) {
        status.textContent = '❌ ' + err.message;
        btn.disabled = false; btn.textContent = '🔁 Reintentar';
      });
    };
  }

  // ===== Main =====
  var fuente = detectarFuente();
  if (!fuente) { alert('Curador AVR: esta página no es de AliExpress ni eBay.\n\nNavega a la ficha del producto y volvé a hacer click en el bookmarklet.'); return; }
  var productId = extraerProductId(fuente);
  if (!productId) { alert('Curador AVR: no pude extraer el ID del producto desde la URL.\n\n¿Estás en una página de FICHA (no de búsqueda ni de categoría)?'); return; }

  // Pide sesion. Si no hay -> login. Si hay y expiro -> refresh. Si refresh falla -> login.
  ensureSession().then(function (sess) {
    if (sess) abrirPopup(fuente, productId, sess);
    else abrirLogin(function (newS) { abrirPopup(fuente, productId, newS); });
  });
})();
