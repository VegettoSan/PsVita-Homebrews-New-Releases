# PsVita Homebrews – New Releases

Monitor automático de nuevos releases de homebrew para PS Vita (GitHub + GitLab).

- Revisa repositorios cada 3 horas
- Notifica en **Discord** cuando sale un release nuevo
- Genera un **dashboard web** con título, versión, body, fecha y botones de descarga de los assets
- El repositorio puede ser privado

## Estructura

```
├── repos.txt                 ← Aquí agregas los repositorios
├── monitor.py                ← Script principal
├── data/
│   ├── state.json            ← Último tag visto por repo
│   └── releases.json         ← Historial de releases
├── docs/
│   └── index.html            ← Dashboard web
└── .github/workflows/
    └── monitor.yml
```

## Configuración rápida

### 1. Secrets necesarios

Ve a **Settings → Secrets and variables → Actions** y crea:

| Secret                 | Obligatorio | Descripción                                      |
|------------------------|-------------|--------------------------------------------------|
| `DISCORD_WEBHOOK_URL`  | Sí          | URL del webhook de tu canal de Discord           |
| `GITHUB_TOKEN`         | Recomendado | Personal Access Token (scope `public_repo`)      |
| `GITLAB_TOKEN`         | Opcional    | Token de GitLab si monitoreas repos privados     |

> El `GITHUB_TOKEN` por defecto de Actions ya se usa para hacer push.  
> El secret `GITHUB_TOKEN` extra es para aumentar el rate limit de la API al consultar otros repos.

### 2. Crear Webhook de Discord

1. Entra a tu servidor de Discord
2. Editar canal → Integraciones → Webhooks → **Nuevo Webhook**
3. Copia la URL y pégala en el secret `DISCORD_WEBHOOK_URL`

### 3. Activar GitHub Pages (aunque el repo sea privado)

1. Ve a **Settings → Pages**
2. Source: **GitHub Actions**
3. Guarda

Como el repositorio es privado, el sitio solo será visible para las personas que tengan acceso al repo.

### 4. Agregar repositorios

Edita `repos.txt`:

```
# GitHub
https://github.com/VegettoSan/PSVitaAlive
TheOfficialFloW/VitaShell

# GitLab
https://gitlab.com/usuario/proyecto
```

### 5. Primera ejecución

Ve a la pestaña **Actions** → workflow **Monitor New Releases** → **Run workflow**.

## Dashboard

Después de la primera ejecución tendrás el dashboard en:

```
https://<tu-usuario>.github.io/PsVita-Homebrews-New-Releases/
```

(si el repo se llama exactamente así)

O puedes abrirlo localmente abriendo `docs/index.html`.

## Características del dashboard

- Título del release
- Versión / tag
- Fecha de publicación
- Body completo (con scroll)
- Botones de descarga de **todos los assets** del release
- Indicador de Pre-release
- Color distinto para GitHub (azul) y GitLab (naranja)

## Notas

- El historial guarda los últimos 100 releases.
- Solo se notifica a Discord cuando el tag cambia (no reenvía el mismo release).
- Puedes ejecutar el workflow manualmente cuando quieras.
