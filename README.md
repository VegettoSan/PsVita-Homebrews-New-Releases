# PS Vita Homebrews — New Releases

Monitor automático de releases de **homebrew, ports, plugins y utilidades de PS Vita**.

La lista de proyectos ya no se mantiene manualmente: cada ejecución descarga el catálogo público de **[PSVitaAlive](https://github.com/VegettoSan/PSVitaAlive)**, descubre los repositorios de **GitHub y GitLab** enlazados por el catálogo y comprueba sus releases.

## Qué hace

- 🔄 Sincroniza los repositorios desde `PSVitaAlive/catalog.json`.
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
https://vegettossan.github.io/PsVita-Homebrews-New-Releases/
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
4. Desde la siguiente ejecución, cualquier release nuevo sí genera notificación.

## Fuente del catálogo

Los metadatos del catálogo de homebrew provienen de PSVitaAlive. El catálogo mantiene registros canónicos por aplicación y enlaces a repositorios/release pages.

Proyecto:

https://github.com/VegettoSan/PSVitaAlive
