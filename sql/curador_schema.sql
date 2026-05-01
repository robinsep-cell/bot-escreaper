-- Esquema para el modulo curador (AliExpress / eBay)
-- Ejecutar en Supabase Studio > SQL Editor

CREATE TABLE IF NOT EXISTS productos_curados (
  id BIGSERIAL PRIMARY KEY,

  -- origen
  fuente TEXT NOT NULL CHECK (fuente IN ('aliexpress','ebay')),
  url_origen TEXT UNIQUE NOT NULL,
  product_id_origen TEXT,
  titulo TEXT,
  imagen_url TEXT,
  vendedor TEXT,
  rating_vendedor FLOAT,

  -- precios componentes
  precio_origen_usd FLOAT,
  envio_usd FLOAT DEFAULT 0,
  impuesto_pct FLOAT DEFAULT 19,             -- IVA Chile por defecto
  costo_total_usd FLOAT,                      -- precio + envio + impuesto
  tipo_cambio_clp FLOAT,                      -- usado al calcular
  costo_total_clp FLOAT,                      -- costo_total_usd * tipo_cambio
  precio_venta_clp FLOAT,                     -- calculado por Robot B
  multiplicador_aplicado FLOAT,               -- para auditoria

  -- curacion
  agregado_por TEXT DEFAULT 'Robin',
  fecha_agregado TIMESTAMPTZ DEFAULT NOW(),
  notas TEXT,
  vehiculos_compatibles TEXT,
  categoria TEXT,

  -- mantenimiento
  ultima_revision TIMESTAMPTZ,
  cambio_precio_alert BOOLEAN DEFAULT FALSE,
  publicado BOOLEAN DEFAULT FALSE,
  activo BOOLEAN DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_curados_fuente ON productos_curados(fuente);
CREATE INDEX IF NOT EXISTS idx_curados_publicado ON productos_curados(publicado) WHERE publicado = TRUE;

-- Historial de precios (cada vez que el vigilante refresca)
CREATE TABLE IF NOT EXISTS precio_historial (
  id BIGSERIAL PRIMARY KEY,
  producto_id BIGINT REFERENCES productos_curados(id) ON DELETE CASCADE,
  fecha TIMESTAMPTZ DEFAULT NOW(),
  precio_origen_usd FLOAT,
  envio_usd FLOAT,
  costo_total_clp FLOAT,
  tipo_cambio_clp FLOAT,
  precio_venta_clp FLOAT
);

CREATE INDEX IF NOT EXISTS idx_historial_producto ON precio_historial(producto_id, fecha DESC);

-- Vista convenience para que el buscador AVR liste todo en una sola query
CREATE OR REPLACE VIEW catalogo_unificado AS
SELECT
  'nacional' AS origen_tipo,
  proveedor,
  categoria,
  url,
  nombre,
  precio,
  imagen,
  fecha_actualizacion
FROM productos_proveedores
UNION ALL
SELECT
  'internacional' AS origen_tipo,
  CONCAT(UPPER(LEFT(fuente,1)), SUBSTRING(fuente FROM 2)) AS proveedor,
  COALESCE(categoria, 'General') AS categoria,
  url_origen AS url,
  titulo AS nombre,
  precio_venta_clp AS precio,
  imagen_url AS imagen,
  COALESCE(ultima_revision, fecha_agregado) AS fecha_actualizacion
FROM productos_curados
WHERE activo = TRUE AND precio_venta_clp IS NOT NULL;
