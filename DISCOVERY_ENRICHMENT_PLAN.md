# Orbit — enriquecimiento automático, fase 1–2

Estado: piloto de solo lectura. No activar publicación, modificar main,
candidatos, checkpoints de Archive.org ni catálogos oficiales hasta validar
resultados.

## Qué contiene
- discovery_enrichment.py: extracción contextual de Title ID, parser PSF
  acotado, evidencia por archivo/línea/ref, resolución de conflictos y
  clasificación ponderada contra las categorías oficiales V1/V2.
- preview_discovery_enrichment.py: reutiliza request(), files_for(),
  official_apps() y to_v1() del descubridor existente. Lee archivos de
  GitHub/GitLab con el SHA del commit, revisa hasta cinco releases
  (incluidos prereleases, no drafts) y comprueba Title ID frente al catálogo
  y registro de retirados. No ejecuta código ni descarga VPK.
- tests/test_discovery_enrichment.py y workflow de CI solo lectura.

## Ejecutar
    python -m unittest discover -s tests -p test_discovery_enrichment.py -v
    python preview_discovery_enrichment.py --limit 5 --max-files 18 --output enrichment-report.json

Usar --offset para recorrer diferentes lotes. Máximo 15 proyectos y 40
archivos por proyecto, además del presupuesto HTTP y de tiempo ya existente.
El piloto prioriza archivos anidados param.sfo, CMakeLists, Makefile,
configuraciones y documentación. No garantiza cobertura completa del árbol
(truncamientos del proveedor, límite de archivos, tamaños y codificación).
Los resultados son sugerencias, no confirmación de identidad de un VPK.
Una referencia a otro Title ID, varios paquetes, forks o conflicto con
catálogo/registro requiere revisión; no escoger el primer ID.

El icono será obligatorio en el futuro validador de publicación. Las capturas
son opcionales (0–5); su ausencia jamás impedirá publicar. Las sugerencias
se contrastan con la taxonomía oficial y no crean categorías. No confundir
un port de un emulador o utilidad con un port de juego.

## Siguiente puerta de validación
Revisar el artefacto enrichment-preview de CI, errores/reintentos, ejemplos
de Title ID y categorías, ampliar cobertura y probar casos reales antes de
integrar la cola persistente. Después: completar los demás metadatos,
validación y simulación de publicación en PSVitaAlive. Reutilizar su
validador/generador, registro de IDs, lock vitahub-catalog-publisher y
sistema de respaldo autorizado existente. No inferir autorización de
redistribución de la mera existencia de un repositorio público.

## Mandatory metadata preflight (2026-11-27)

The live PSVitaAlive source validator requires non-empty long_description and
subcategory_ids, positive integer size, real version/date, existing authors,
category, requirements, status and links. The automation additionally requires
an application icon; empty screenshots are allowed. Reuse the short description
as long_description when necessary; use the official subcategory other only if
it exists within the selected category; use exactly "- no data" if runtime
requirements are unknown. Do not invent release data, authors, download links,
or status. New author drafts and status policy require approval. The readiness
report lists missing required fields before optional data. A complete metadata
report does not authorize publication; the canonical validator and additional
identity/media checks remain required. Reports are read-only and do not modify
candidates or canonical sources.
