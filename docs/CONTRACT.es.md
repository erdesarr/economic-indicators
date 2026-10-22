# Contrato de API — JSON de indicadores

**[English](CONTRACT.md) | Español**

Dos modos de lectura exponen **el mismo modelo**:

1. **FastAPI** (`src/api/main.py`), SQLite de solo lectura, OpenAPI en `/docs`.
2. **Contrato estático** generado por `scripts/build_pages.py` y publicado en
   GitHub Pages bajo `/api/v1/`.

URLs base:

```
FastAPI:  http://127.0.0.1:8000/api/v1        (local) o tu despliegue
Pages:    https://<usuario>.github.io/<repo>/api/v1
```

## Objeto de lectura común

Cada lectura (latest, ítem de historial) tiene estos campos:

| Campo | Tipo | Notas |
|---|---|---|
| `slug` | string | id del indicador, p. ej. `ibr_overnight` |
| `date` | `YYYY-MM-DD` | fecha del run en America/Bogota |
| `value` | number | REAL normalizado; IBR = serie **nominal** |
| `value_published` | number \| null | valor de referencia publicado (IBR = efectivo `% E.A.`) |
| `unit` | string | unidad canónica del catálogo, p. ej. `COP`, `% nominal anual` |
| `unit_as_published` | string | unidad tal como la muestra la fuente (`DÓLARES / BARRIL`, `Valores en EUR`, `%`, …) |
| `status` | `ok` \| `forward_filled` \| `manual` | estado de la lectura |
| `base_gap` | boolean | true cuando la base de variación no es el día natural anterior |
| `source_date` | `YYYY-MM-DD` \| null | fecha que muestra la fuente (best effort) |
| `variation_pct` | number \| null | `(v_hoy - v_base) / v_base * 100`; null sin base o división por cero |

Semántica de variación: la base es el día natural anterior cuando existe
lectura; si no, la lectura más reciente dentro de `max_gap_days` (7) con
`base_gap = true`; sin lectura → `variation_pct = null`.

## Archivos estáticos

| Archivo | Contenido |
|---|---|
| `v1/indicators.json` | catálogo: `generated_at` + `indicators[]` con `slug`, `source_url`, `visible_label`, `unit`, `latest` |
| `v1/latest.json` | `generated_at` + objeto `indicators` por slug → lectura |
| `v1/indicators/<slug>/latest.json` | lectura o `{}` si no hay datos |
| `v1/indicators/<slug>/history.json` | `slug`, `unit`, `from_date`, `to_date`, `readings[]` (ventana de 31 días) |
| `v1/indicators/<slug>/variation.json` | `slug`, `date`, `value`, `previous_date`, `previous_value`, `variation_pct`, `base_gap` |

### Ejemplo — `v1/indicators/ibr_overnight/latest.json`

```json
{
  "slug": "ibr_overnight",
  "date": "2026-10-08",
  "value": 11.407,
  "value_published": 12.259,
  "unit": "% nominal anual",
  "unit_as_published": "%",
  "status": "ok",
  "base_gap": false,
  "source_date": "2026-10-08",
  "variation_pct": -0.0351
}
```

### Ejemplo — `v1/latest.json` (extracto)

```json
{
  "generated_at": "2026-10-08T19:00:00+00:00",
  "indicators": {
    "dolar_oficial_hoy": { "slug": "dolar_oficial_hoy", "value": 3238.88, "unit": "COP", "...": "..." },
    "gas_ttf_nl": { "slug": "gas_ttf_nl", "value": 77.205, "unit": "EUR/MWh", "...": "..." }
  }
}
```

## Endpoints FastAPI

| Método | Ruta | Respuesta |
|---|---|---|
| GET | `/api/v1/indicators` | `IndicatorsResponse` (catálogo + latest por indicador) |
| GET | `/api/v1/indicators/{slug}/latest` | objeto lectura (404 si no existe o está vacío) |
| GET | `/api/v1/indicators/{slug}/history?from=&to=` | `HistoryResponse` |
| GET | `/api/v1/indicators/{slug}/variation` | `VariationResponse` |
| GET | `/docs` | Swagger UI con ejemplos de datos reales (run 2026-10-08) |
| GET | `/openapi.json` | esquema OpenAPI 3.1 |

## Cabeceras y caché

- **CORS:** FastAPI envía `Access-Control-Allow-Origin: *`. GitHub Pages sirve
  los JSON estáticos con las cabeceras que GitHub provee; no se pueden
  configurar cabeceras `Cache-Control`/CORS personalizadas en Pages. Si
  necesitas `Cache-Control: no-cache` garantizado, usa el modo FastAPI detrás de
  tu propio proxy.
- La conexión SQLite de FastAPI se abre en modo `ro` (URI solo lectura).

## Estados y confiabilidad

- `status='ok'`: extracción fresca.
- `status='forward_filled'`: la fuente falló; se copió la última lectura
  almacenada y `reason` lo explica. Si no hay lectura previa, el indicador no
  aparece ese día (nunca se inventa).
- `status='manual'`: reservado para correcciones del dueño.
- Valores fuera de `plausible_range` se tratan como fallo de parseo
  (forward-fill). El check del scrape pingea `/fail` solo si el fallo está
  dentro del `publication_window` del indicador; fuera de la ventana el ping es
  success con warning en el body.
