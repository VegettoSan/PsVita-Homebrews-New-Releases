# PS Vita Homebrews — New Releases

Monitor automático de releases de **homebrew, ports, plugins y utilidades de PS Vita**.

La lista de proyectos ya no se mantiene manualmente: cada ejecución descarga el catálogo público de **[PSVitaAlive](https://github.com/VegettoSan/PSVitaAlive)**, descubre los repositorios de **GitHub y GitLab** enlazados por el catálogo y comprueba sus releases.


## Sincronización inmediata con PSVitaAlive y actualización de Homebrew

Además del cron de tres horas, el monitor ahora acepta el evento
`psvitaalive_catalog_published`. Cuando el workflow de publicación de PSVitaAlive
termina correctamente, avisa al monitor enviando el SHA del catálogo publicado.
El monitor lo consulta directamente, sincroniza los repos y reconstruye la web.

Cada ejecución completa genera también `data/upstream_latest.json` (snapshot de
releases actuales de los repositorios monitoreados). Puede emitir el evento
`release_monitor_synced` a PSVitaAlive para que su workflow **valide y actualice
solo JSON canónicos seguros**, nunca los catálogos generados a mano.

Para conectar ambos repositorios, se requieren dos secrets de Actions con tokens
de alcance limitado:

- `PSVA_TRACKER_DISPATCH_TOKEN`, guardado en **PSVitaAlive** y con permiso
  Contents: Read and write *solo* en este repositorio de releases.
- `PSVA_AUTOUPDATE_DISPATCH_TOKEN`, guardado en **este monitor** y con permiso
  Contents: Read and write *solo* en `VegettoSan/PSVitaAlive`.

Sin estos secrets, las sincronizaciones programadas continúan, pero los eventos
entre repositorios no se pueden enviar.

Los filtros de seguridad, las exclusiones de apps con Game Files y el orden de
activación están documentados en
[PSVitaAlive/docs/AUTOMATIC_RELEASE_CATALOG_SYNC.md](https://github.com/VegettoSan/PSVitaAlive/blob/main/docs/AUTOMATIC_RELEASE_CATALOG_SYNC.md).

### Comprobación manual del monitor

Además de la ejecución programada cada tres horas, el workflow `PS Vita Release Tracker`
se puede iniciar desde GitHub Actions con **Run workflow**. Una confirmación
administrativa mediante un commit a `main` con `[scan]` en el mensaje también
fuerza el escaneo completo y la regeneración del dashboard; los commits normales
no realizan esa consulta costosa.

## Actualizaciones automáticas de GitHub y GitLab

El monitor identifica releases en **GitHub y GitLab** y remite sus assets al
actualizador de PSVitaAlive. Si la versión es más nueva y la release expone
**exactamente un .vpk**, el actualizador puede reemplazar **solo la URL
del botón `Download` llamado `VPK`**, sin cambiar su nombre, preferencia,
posición ni otros botones de configuraciones alternativas. No añade Mirrors
ni botones con enlaces antiguos; tampoco descarga el VPK.

Cuando una release no tiene VPK o tiene varios, se mantiene **Pendiente** y
la misma ficha explica que requiere revisión manual. En GitLab se priorizan
los enlaces permanentes `direct_asset_url` de los adjuntos. Los enlaces de
descarga externos, proyectos ambiguos y VPK sin botón `VPK` inequívoco en
el JSON se omiten de la automatización para revisión.

**El enlace manual de Archive.org nunca se sobrescribe mientras la versión
del catálogo sea igual o superior a la release monitoreada.** Si llega otra
versión nueva válida, el enlace `VPK` puede pasar temporalmente a la
descarga del repositorio hasta que se haga su respaldo manual.

## Preservación en la misma ficha de releases

**No hay sección ni página nueva** para los respaldos: cada tarjeta del monitor
mantiene los botones de descarga, el estado de actualización de PSVitaAlive y
una segunda etiqueta de Archive.org.

- **Respaldo en Archive.org**: el Download actual/recomendado del JSON apunta a
  un VPK de Archive.org, y el release monitoreado no es más nuevo que la versión
  del catálogo. La etiqueta es un enlace pulsable hacia ese VPK.
- **Versión nueva sin respaldo**: el enlace principal aún apunta a Archive.org
  de la versión del catálogo, pero el upstream ya anunció otra versión.
- **Falta respaldo en Archive.org**: tras actualizarse el catálogo a un release
  nuevo, el botón `VPK` actual apunta a GitHub/GitLab en lugar de Archive.org.
  Si ya había otros Mirrors manuales, se conservan sin crear otros nuevos.
- **Revisar respaldo**: falta una descarga principal inequívoca o hay
  incongruencia comprobable con el nombre del VPK/versiones.
- Los registros sin ficha asociada no presentan esta etiqueta.

Los VPK **antiguos sin versión en su nombre** no se tratan como perdidos cuando
son el Download de la versión catalogada. Los nuevos nombres con versión sirven
como comprobación adicional, no como requisito para aceptar un respaldo.

**No es una verificación técnica del contenido:** la etiqueta refleja
la URL registrada por quien mantiene PSVitaAlive, no disponibilidad real,
checksum ni verificación del Title ID. No descarga ni sube archivos y no
necesita credenciales de Archive.org.

### Filtros en la misma página

La barra de filtros existente incluye búsqueda por texto y plataforma,
**versión en PSVitaAlive** (todas / al día / pendiente de actualizar)
y **respaldo en Archive.org**:

- Todos los respaldos.
- Pendientes de respaldar: agrupa `missing` y `outdated`.
- En Archive.org: el VPK de descarga actual está enlazado allí.
- Sin respaldo actual: el Download vigente no es un VPK de Archive.org.
- Nueva versión sin respaldo: el upstream ha lanzado una versión posterior a
  la versión que está respaldada en el enlace actual.
- Revisar respaldo: situaciones que no permiten clasificarlo con seguridad.
- Sin ficha asociada: no hay un Homebrew de PSVitaAlive enlazado a ese release.

**Los filtros se pueden combinar**: «Al día en PSVitaAlive» +
«Pendientes de respaldar» enseña precisamente versiones ya actualizadas en
el catálogo pero que aún necesitan su copia de Archive.org. Los desplegables
incluyen conteos globales, el contador de resultados cambia al filtrar y el
botón «Limpiar filtros» restablece búsqueda y selección.

Los futuros descubrimientos de Homebrew ausentes del catálogo tendrán una
pestaña separada cuando implementemos esa funcionalidad, no antes.

## Segunda pestaña: búsqueda de Game Data

La web mantiene dos pestañas: **Actualizaciones Homebrew** (el monitor
original, sin cambios en sus filtros) y **Búsqueda de Game Data**.

**Solo aparecen ports con alguna referencia documentada** y que no tengan ya
un enlace Game Files, Data Files ni un data_url en el catálogo oficial. Los
ports sin evidencia se contabilizan internamente, pero no aparecen como
tarjetas vacías.

Fuentes reconocidas:

- **GitHub/GitLab Releases:** un asset de archivo cuyo nombre indica Game
  Files/Data Files. Se muestra el release del desarrollador; no se descarga
  el archivo ni se verifica su contenido.
- **PS Vita Ports Tracker [Official] (hoja pública de u/RamenRams):**
  https://docs.google.com/spreadsheets/d/1_nuD1Sr5sNCtZPjWMliR-fFZDvQToUtnMwhh_dJkNeE/edit?gid=1739999294
  Publicada en Reddit:
  https://www.reddit.com/r/VitaPiracy/comments/1xd0dai/full_list_of_ps_vita_ports_google_sheets/
  Cada ejecución lee su CSV público y selecciona filas con **enlaces concretos
  de descarga**. Distingue referencias a archivos de datos/ZIP de paquetes
  generales: según la publicación original de Reddit, muchos de estos
  últimos incluyen VPK y datos conjuntamente, pero el escáner **no verifica
  ese contenido**. Para asociar las filas a PSVitaAlive compara el repositorio
  del desarrollador, reconoce títulos alternativos cuando el repositorio
  corresponde de forma inequívoca o aplica un nombre exacto si no hay
  colisiones ni repositorios contradictorios. También identifica el nombre
  original del juego desde los campos `description` (descripción corta)
  y `long_description` (descripción larga) del catálogo PSVitaAlive V1/V2,
  cuando mencionan explícitamente que es un **port de ese título**.
  Una comparación ("inspirado en...", "similar a...") o una mención
  accidental no basta. Una coincidencia por descripción solo se acepta si
  no hay otra aplicación compatible que la haga ambigua, y nunca invalida
  un repositorio ya identificado con otra aplicación. Se muestra en la
  tarjeta el nombre original que figura en la hoja y el método usado para
  vincularlo; siempre requiere revisión humana. Descarta Missing y WIP.
  Los enlaces conducen a **la fila pública de la hoja**, nunca a una descarga
  externa sin revisar.

Los hallazgos de Issues, problemas de ejecución y textos genéricos que
mencionen "data files" ya no se muestran como evidencia. Las fuentes de la
hoja comunitaria **no son una verificación independiente de integridad,
legalidad, licencia o compatibilidad**. Se identifican separadamente de los
assets de releases oficiales. Los registros canónicos de PSVitaAlive siguen
siendo la única autoridad para el catálogo.

El workflow **Discover missing PS Vita Game Data** se ejecuta cada tres horas
a minuto 47 UTC, separado del monitor de releases (minuto 17). También puede
ejecutarse manualmente. Inspecciona todos los ports con repositorios identificables
(hasta 500 por ronda), incluidos algunos repositorios deducidos de enlaces
oficiales de releases; la hoja se consulta una vez por ejecución. Si la hoja no responde, el escaneo de
GitHub/GitLab continúa y preserva las referencias de hoja de la última
consulta exitosa, señalando el error en el JSON.

El JSON de resultados generado es data/game_data_candidates.json y el
dashboard está en docs/index.html. El código no modifica los archivos
canónicos de PSVitaAlive ni sus catálogos generados.

**Discord:** la búsqueda Game Data nunca envía mensajes o notificaciones.
Las notificaciones del monitor original de releases continúan funcionando.
La consulta programática de Reddit requeriría una integración OAuth
aprobada; por ahora solo se consulta la **hoja pública enlazada por Reddit**
y se facilita la búsqueda manual desde cada tarjeta.

## Qué hace

- 🔄 Sincroniza los repositorios desde `PSVitaAlive/catalog.json`, tanto en formato de claves largas (V1) como compactas (V2).
- ⏱️ Se ejecuta automáticamente **cada 3 horas** con GitHub Actions.
- 🐙 Soporta repositorios de **GitHub**.
- 🦊 Soporta proyectos de **GitLab**, incluidos grupos/subgrupos.
- 🔔 Envía a **Discord** únicamente los releases nuevos.
- 🧯 La primera sincronización completa es silenciosa para no inundar Discord con releases antiguos.
- 📦 Destaca assets típicos de Vita como `.vpk`, `.skprx`, `.suprx`, `.self` y `.zip`.
- 🌐 Genera un dashboard responsive en `docs/index.html`.
- 🔎 La web incluye búsqueda y filtro por plataforma.
- 🧾 Guarda hasta los **500 releases** más recientes.
- 🛡️ Un repositorio caído o eliminado no detiene el resto de la ejecución.

## Flujo

```text
PSVitaAlive/catalog.json
        │
        ▼
 descubrir GitHub/GitLab
        │
        ├──► repos.txt
        │
        ▼
 consultar releases
        │
        ├──► data/state.json
        ├──► data/releases.json
        ├──► data/sync.json
        ├──► Discord
        └──► docs/index.html ──► GitHub Pages
```

## Archivos principales

```text
├── .github/workflows/monitor.yml
├── monitor.py
├── requirements.txt
├── repos.txt                 # Generado automáticamente
├── data/
│   ├── state.json
│   ├── releases.json
│   └── sync.json             # Generado en la primera ejecución de la V2
└── docs/
    └── index.html
```

> `repos.txt` es un archivo generado. No es necesario agregar repositorios manualmente mientras estén enlazados desde el catálogo de PSVitaAlive.

## Configuración de Discord

En el repositorio abre:

**Settings → Secrets and variables → Actions → New repository secret**

y crea:

```text
DISCORD_WEBHOOK_URL
```

El webhook **no debe escribirse en `monitor.py`, en el workflow ni en ningún archivo público**.

### Tokens

El workflow usa automáticamente `${{ secrets.GITHUB_TOKEN }}` proporcionado por GitHub Actions.

`GITLAB_TOKEN` es opcional y solo hace falta si en el futuro se quieren consultar proyectos privados o aumentar acceso en GitLab.

## GitHub Pages

La web se publica desde el artefacto generado en `docs/`.

En:

**Settings → Pages → Build and deployment → Source**

selecciona:

```text
GitHub Actions
```

La URL esperada es:

```text
https://vegettosan.github.io/PsVita-Homebrews-New-Releases/
```

> GitHub transforma el nombre de usuario a minúsculas en el dominio.

## Ejecución manual

También puedes ejecutar el monitor desde:

**Actions → PS Vita Release Tracker → Run workflow**

o localmente:

```bash
python -m pip install -r requirements.txt
python monitor.py
```

## Variables opcionales

| Variable | Uso |
|---|---|
| `DISCORD_WEBHOOK_URL` | Webhook de notificaciones |
| `GITHUB_TOKEN` | Token para la API de GitHub |
| `GITLAB_TOKEN` | Token opcional para GitLab |
| `PSVITAALIVE_CATALOG_URL` | Permite cambiar temporalmente la fuente del catálogo |

## Comportamiento de la primera sincronización

El repositorio ya tenía un estado previo con proyectos monitorizados. Al migrar a la sincronización completa de PSVitaAlive pueden aparecer muchos repositorios adicionales.

Para evitar spam:

1. Los repos recién descubiertos se incorporan al estado.
2. Sus releases actuales se añaden al dashboard.
3. **No se envían cientos de notificaciones históricas.**
4. Los repositorios añadidos más adelante también se inicializan sin avisos históricos.
5. Las actualizaciones de repositorios previamente monitorizados sí pueden generar notificaciones.

## Protección de datos ante cambios de esquema

El monitor decodifica las claves compactas del catálogo oficial mediante `catalog_schema_v2_compat.py`.
Si un cambio de esquema provoca que no se descubran repositorios, o desaparece repentinamente
más de la mitad de los repositorios conocidos, el workflow **falla sin sobrescribir**
`repos.txt`, `data/state.json` ni el historial. Esto evita que una ejecución incorrecta
reinicie los avisos. El dashboard realiza una validación similar antes de guardar datos.

`.github/workflows/catalog-schema-v2-compat.yml` incluye pruebas de regresión y una
comprobación de integración contra el catálogo público actual.

## Fuente del catálogo

Los metadatos del catálogo de homebrew provienen de PSVitaAlive. El catálogo mantiene registros canónicos por aplicación y enlaces a repositorios/release pages.

Proyecto:

https://github.com/VegettoSan/PSVitaAlive
