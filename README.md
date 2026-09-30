# Lupita

Agregador costarricense de noticias con RSS, Ollama y Hugo. Requiere Python
3.11 o posterior; Docker Compose es opcional.

## Configuración

```sh
cp .env.example .env
```

Configura `OLLAMA_MODEL` y `OLLAMA_BASE_URL`. La configuración se aplica en este
orden: opciones CLI, variables del proceso, `.env` y valores predeterminados.

| Variable | Uso | Valor predeterminado |
| --- | --- | --- |
| `NEWS_SOURCE` | Fuente o `all` | `all` |
| `NEWS_FROM` / `NEWS_UNTIL` | Rango de publicación | Desde ayer hasta ahora |
| `NEWS_LIMIT` | Máximo por etapa; `0` no limita | `0` |
| `NEWS_DRAFT` | Mantener borradores después de redactar | `true` |
| `NEWS_INTERVAL` | Pausa entre ciclos de `serve`, en segundos | `28800` |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | Servidor y modelo | `http://localhost:11434` / requerido |
| `OLLAMA_TIMEOUT` | Tiempo máximo por consulta, en segundos | `180` |
| `PEXELS_API_KEY` | Habilita la búsqueda de imágenes | Vacía |
| `CONTENT_DIR` / `STATE_DIR` | Contenido y estado local | `content` / `.pipeline` |

Las fechas usan `America/Costa_Rica`. El rango acepta fechas (`YYYY-MM-DD`),
instantes ISO con zona horaria y `yesterday`, `today`, `now` o `all`.
Los scrapers aplican los filtros disponibles en cada feed antes de procesar
las noticias.

## Comandos

Ejecuta con `PYTHONPATH=src python3 -m lupita` o `docker compose run --rm
--build ingest` seguido del comando:

| Comando | Acción |
| --- | --- |
| `scrape` | Consulta RSS y devuelve JSON, sin IA |
| `index` | Guarda una instantánea JSON de noticias |
| `stage` | Crea borradores y referencias locales |
| `process` | Redacta los borradores pendientes |
| `ingest` | Ejecuta `stage` y `process` |
| `serve` | Repite el flujo a intervalos y reintenta pendientes |
| `images` | Busca imágenes de Pexels para noticias publicadas |

```sh
# Procesar noticias recientes
docker compose run --rm --build ingest

# Crear borradores y procesarlos por separado
docker compose run --rm --build ingest stage
docker compose run --rm ingest process

# Consultar Ollama sin escribir archivos
docker compose run --rm ingest process --dry-run --limit 5

# Importar un RSS o índice local
PYTHONPATH=src python3 -m lupita scrape --source nacion --feed-file feed.xml
PYTHONPATH=src python3 -m lupita stage --input .news-index.json
```

Fuentes disponibles: `nacion`, `delfino`, `semanario`, `teletica`,
`elfinanciero`, `observador`, `diarioextra`, `ncrnoticias`, `elmundo` y
`repretel`. `--feed-file` requiere una fuente concreta; `--input` se usa con
`stage` o `ingest`.

## Servicio periódico

```sh
docker compose up -d --build worker
docker compose logs -f worker
docker compose stop worker
```

El worker ejecuta `serve` al iniciar y repite el ciclo según `NEWS_INTERVAL`.
Para publicar los artículos redactados, configura `NEWS_DRAFT=false` y recrea
el worker. Para habilitar imágenes, configura `PEXELS_API_KEY`.

## Ramas de revisión

Los comandos `ingest`, `stage`, `process` y `serve` admiten `--branch` y
`--repo` para subir cambios a una rama de revisión. La autenticación usa
`GITHUB_TOKEN` o `--ssh-key` (`NEWS_SSH_KEY`); no se pueden combinar. El flujo
remoto trabaja en una rama y no escribe en `main`.

```sh
GITHUB_TOKEN="$GITHUB_TOKEN" PYTHONPATH=src python3 -m lupita stage \
  --branch ai-editor-stage
```

## Contenido y sitio

Las noticias se identifican por URL normalizada. Se conservan título, autores
y fuente; el cuerpo original no se publica. `.pipeline/` guarda referencias
para reintentar y `.news-index.json` contiene datos RSS locales: mantenlos
privados y excluidos de Git.

Hugo compila el sitio; se requieren Hugo 0.166.0 y Node.js 22 o posterior.
Para una vista previa:

```sh
hugo server --buildDrafts
```

Para compilar el sitio y su índice de búsqueda:

```sh
sh scripts/build.sh
```

El despliegue estático puede servirse desde `public/`. Las fotos de Pexels son
ilustrativas y muestran su atribución en el pie.

Edita las [instrucciones editoriales](src/lupita/editor_instructions.md) para
cambiar la redacción y clasificación. El catálogo y la validación están en
[editor.py](src/lupita/editor.py).
