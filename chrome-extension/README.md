# Curador AVR — Extensión Chrome (refresh automático)

Extensión que refresca precios de productos curados de AliExpress en background, usando tu sesión real de Chrome (bypaseando anti-bot porque NO es un scraper externo).

## Cómo funciona

1. Cada 12 horas, automáticamente:
2. Lee de Supabase los productos con `ultima_revision` más vieja que 18h (máximo 25 por corrida)
3. Para cada uno, abre la URL en una pestaña en una **ventana minimizada** de tu Chrome
4. Espera que la página renderice
5. Extrae precio + envío del DOM real (con tu sesión)
6. Calcula nuevo costo + PV con tu fórmula
7. Hace PATCH al row de Supabase + agrega entrada en `precio_historial`
8. Cierra la pestaña

Solo corre cuando tu Chrome está abierto.

## Instalación (5 minutos, una vez)

1. Abrí Chrome y andá a: **chrome://extensions**
2. Arriba a la derecha, prendé el toggle **"Modo de desarrollador"**
3. Click **"Cargar descomprimida"** (botón a la izquierda)
4. Seleccioná esta carpeta: `chrome-extension/` (la que estás leyendo)
5. ¡Listo! Te aparece la extensión en la lista

Vas a ver un ícono nuevo arriba a la derecha (puede estar oculto detrás del 🧩 de extensiones — pinchá ese y "fijá" la extensión en la barra).

## Uso

### Automático
La extensión corre sola cada 12h. La primera corrida es 2 minutos después de instalarla.

### Manual
1. Click en el ícono de la extensión
2. Vas a ver el estado: última corrida, cuántos productos refrescó, cuántos fallaron
3. Click en **"🔄 Refrescar ahora"** para correr al toque
4. Verás cómo se actualizan los stats en vivo

### Debug
Si querés ver qué está pasando:
1. `chrome://extensions`
2. En la card de "Curador AVR", click en **"Service worker"** (texto azul)
3. Se abre DevTools mostrando los logs del background script

## Actualizar la extensión

Cuando el código cambie en GitHub, vos hacés:
```bash
cd "/Users/robinsonsepulveda/Proveedores Multiproductos/Bot Escreaper"
git pull
```
Luego en `chrome://extensions` → click en el ícono 🔄 de "Curador AVR" para recargar.

## Configuración interna

En [`background.js`](background.js) podés cambiar:
- `PERIOD_HOURS` (default 12) — cada cuánto corre
- `STALENESS_HOURS` (default 18) — antigüedad mínima para considerar "refrescable"
- `MAX_PER_RUN` (default 25) — productos por corrida
- `DELAY_BETWEEN_MS` (default 8000) — pausa entre productos para ser cortés con AliExpress

Después de cambiar estos, recargá la extensión en `chrome://extensions`.

## Limitaciones honestas

- Solo corre cuando Chrome está abierto. Si cerrás Chrome de noche, no refresca.
- Si AliExpress cambia el DOM, los selectores se rompen — hay que actualizar [`background.js`](background.js) (función `extractAliexpress`).
- Si tenés muchos productos (>500) la corrida tarda mucho (25 por ciclo = 30+ min). Subí `MAX_PER_RUN` con cuidado.
- Si no estás logueado en AliExpress, sigue funcionando para leer precios públicos.

## ¿Por qué Manifest V3?

Chrome eliminó Manifest V2 en 2024. V3 usa service workers (eventuales, no persistentes), API `chrome.alarms` para schedule, y `chrome.scripting.executeScript` para inyectar código en pestañas. Esto significa que la extensión "se duerme" cuando no tiene nada que hacer y se "despierta" con la alarma — eficiente y compatible.
