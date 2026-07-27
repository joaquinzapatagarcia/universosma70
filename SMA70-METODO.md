# Mecanismo de trabajo SMA70

## Objetivo

Calcular de forma reproducible el mapa semanal de 24 mercados sin inventar cierres, medias, porcentajes, cruces ni barras. El mecanismo actualiza datos; no publica el informe ni modifica el SITE.

## Archivos

- `config/markets.json`: universo fijo, símbolos, equivalencias y regla semanal.
- `scripts/build_sma70.py`: descarga históricos diarios, forma semanas completas y calcula SMA70.
- `data/sma70-latest.json`: salida estructurada para el informe y el SITE.
- `data/sma70-latest.md`: informe humano de control.
- `.github/workflows/refresh-sma70.yml`: ejecución semanal y manual.

## Convención matemática

Para cada instrumento:

1. Se agrupan cierres diarios por semana de mercado.
2. Solo se aceptan semanas concluidas.
3. Se toma el último cierre disponible de cada semana completa.
4. `SMA70 = media simple de los 70 cierres semanales completos más recientes`.
5. `Distancia = ((último cierre semanal / SMA70) - 1) × 100`.
6. `Cambio semanal = ((último cierre / cierre semanal anterior) - 1) × 100`.

Zonas:

- `above`: distancia superior a +5 %.
- `zone`: distancia entre −5 % y +5 %, incluidos los límites.
- `below`: distancia inferior a −5 %.

El cruce se compara con el estado calculado para la semana anterior usando su propia SMA70 de aquel momento.

## MUNDO

MUNDO utiliza `ACWI`, ETF global ponderado que replica el MSCI ACWI. Es una equivalencia operativa, no el nivel oficial del índice MSCI. Nunca se obtiene como media simple de los otros 23 mercados.

La salida MUNDO contiene:

- cierre semanal;
- SMA70;
- distancia;
- cambio semanal;
- estado;
- cruce;
- metadatos de fuente.

## Amplitud

La amplitud se calcula como el porcentaje de mercados verificados —excluido MUNDO— que están más de un 5 % por encima de su SMA70.

El resumen genera además:

- recuento por encima, en zona y por debajo;
- cinco más fuertes;
- cinco más débiles;
- cinco más próximos a la SMA70;
- cruces y cambios de zona.

## Fuente y contraste

La fuente operativa es el endpoint público de históricos de Yahoo Finance. La salida conserva símbolo, mercado, zona horaria y URL de consulta. Antes de una publicación editorial, los cierres críticos —especialmente MUNDO, cruces y valores próximos al umbral— deben contrastarse con la bolsa, el proveedor del índice o una segunda fuente.

## Regla de publicación

`summary.publication_ready` solo es verdadero cuando los 24 instrumentos están verificados y MUNDO dispone de cálculo completo.

Cuando sea falso:

- el informe puede redactarse normalmente;
- los datos disponibles pueden citarse con su estado;
- no deben inventarse mercados faltantes;
- el mapa completo, el radar y el COLOR SMA70 deben marcarse como parciales o pendientes;
- el SITE no debe presentar el mapa como edición semanal validada.

## Ejecución

Automática: cada sábado a las 05:15 UTC, después del cierre semanal de los mercados principales.

Manual: GitHub → Actions → `Actualizar mapa SMA70` → `Run workflow`.

Local:

```bash
python scripts/build_sma70.py
```

Para una fecha histórica:

```bash
python scripts/build_sma70.py --date 2026-07-24
```

Modo de control estricto:

```bash
python scripts/build_sma70.py --strict
```

El modo estricto devuelve código de error si falta algún instrumento, pero mantiene los archivos de diagnóstico para saber qué símbolo o mercado requiere reparación.

## Flujo editorial diario

1. Leer `data/sma70-latest.json`.
2. Confirmar `generated_at`, `as_of_date` y `publication_ready`.
3. Verificar el cierre semanal usado por cada mercado.
4. Redactar el informe completo de actualidad.
5. Integrar el mapa solo con datos marcados `verified`.
6. Derivar el COLOR SMA70 de MUNDO, amplitud, dirección semanal, riesgo dominante y grado de resistencia.
7. Revisar editorialmente.
8. Publicar únicamente después de autorización expresa.

## Seguridad

El workflow no toca `index.html` ni `dificil-de-mover.html`. Solo escribe los dos archivos de datos. La publicación continúa siendo manual y requiere aprobación en el chat.
