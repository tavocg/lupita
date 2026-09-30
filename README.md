# Lupita

Agregador costarricense con Hugo, RSS y resúmenes redactados con Ollama.
Python 3.11+ sin dependencias; Docker Compose es opcional.

## Configuración

```sh
cp .env.example .env
mkdir -p content .pipeline
```

Configura `OLLAMA_MODEL` y `OLLAMA_BASE_URL`; en Docker ajusta `LOCAL_UID` y
`LOCAL_GID` con `id -u` / `id -g`. Compose usa la red del host (en Docker Desktop,
habilita [host networking](https://docs.docker.com/engine/network/drivers/host/)).

Prioridad: **opciones CLI → variables del proceso → `.env` → valores por defecto**.
Las rutas en opciones/variables se resuelven desde el directorio de ejecución;
en Docker deben existir dentro del contenedor.

| Variable | Opción / uso | Por defecto |
| --- | --- | --- |
| `NEWS_COMMAND` | Comando sin argumento posicional | `ingest` |
| `NEWS_SOURCE` | `--source` | `all` |
| `NEWS_LIMIT` | `--limit`: `0`/`all` sin límite; positivo limita cada etapa | `0` |
| `NEWS_FROM` | `--from`: inicio inclusivo | `yesterday` |
| `NEWS_UNTIL` | `--until`: fin inclusivo | `now` |
| `NEWS_INTERVAL` | `--interval`: espera entre ciclos, en segundos | `28800` (8 h) |
| `NEWS_DRY_RUN` | `--dry-run` / `--no-dry-run`, en ingest/process | `false` |
| `NEWS_DRAFT` | Conservar borradores tras la IA; `false` publica | `true` |
| `PEXELS_API_KEY` | Clave API para buscar fotos ilustrativas | Vacía: búsqueda desactivada |
| `NEWS_IMAGE_LIMIT` | Máximo de noticias sin imagen por ciclo | `2` |
| `NEWS_INPUT` / `NEWS_FEED_FILE` | `--input` JSON / `--feed-file` RSS, excluyentes | Vacíos |
| `NEWS_INDEX_PATH` | `--output` para index | `.news-index.json` |
| `CONTENT_DIR` / `STATE_DIR` | Directorios para Python local | `content` / `.pipeline` |
| `CONTENT_VOLUME` / `STATE_VOLUME` | Directorios del servidor montados por Compose | `./content` / `./.pipeline` |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | Servidor y modelo de IA | `http://localhost:11434` / obligatorio |
| `OLLAMA_TIMEOUT` | Tiempo máximo por petición, segundos | `180` |
| `EDITOR_INSTRUCTIONS_FILE` | Archivo UTF-8 alternativo de instrucciones | Integradas |
| `SERVICE_STOP_GRACE_PERIOD` | Tiempo que Docker espera al detener worker | `5m` |

El rango se aplica a la **fecha de publicación**, antes del límite y de la IA.
Por defecto abarca desde ayer a las 00:00 hasta ahora, en `America/Costa_Rica`.
Admite `YYYY-MM-DD` (incluye todo el día final), instantes ISO con zona como
`2026-09-29T14:00:00-06:00`, `yesterday`, `today`, `now` y `all` para quitar una
frontera. El rango relativo se recalcula en cada ciclo. Los RSS solo ofrecen su
historial disponible; elegir fechas antiguas no recupera archivos del medio.

## Comandos

Usa `PYTHONPATH=src python3 -m lupita` o `docker compose run --rm --build ingest`,
seguidos del comando y sus opciones:

| Comando | Acción |
| --- | --- |
| `index` | Reemplaza atómicamente el JSON de referencia, sin IA ni Markdown |
| `scrape` | Escribe registros JSON en stdout, sin IA |
| `stage` | Crea borradores y referencias privadas, sin IA |
| `process` | Redacta pendientes del rango, sin consultar RSS |
| `ingest` | Combina stage y process para las noticias seleccionadas |
| `serve` | Importa periódicamente y procesa/reintenta todos los pendientes |
| `images` | Busca y asigna fotos Pexels a las últimas noticias publicadas sin imagen |

```sh
# Índice de un rango concreto, sin límite
docker compose run --rm --build index --from 2026-09-28 --until 2026-09-30
# Crear borradores recientes; procesarlos y publicar según NEWS_DRAFT
docker compose run --rm --build ingest stage
docker compose run --rm ingest process
# Buscar fotos para las dos noticias publicadas más recientes que aún no tengan
# imagen (requiere PEXELS_API_KEY en .env).
docker compose run --rm --build images
# Consultar IA sin escribir ni adquirir bloqueo
docker compose run --rm ingest process --from all --dry-run --limit 5
# Importar un índice local, sin scrapers y conservando el archivo de entrada
PYTHONPATH=src python3 -m lupita stage --input .news-index.json --from all
# Reintentar todos los pendientes, independientemente de su antigüedad
PYTHONPATH=src python3 -m lupita process --from all --until all
```

Fuentes: `all`, `nacion`, `delfino`, `semanario`, `teletica`, `elfinanciero`,
`observador`, `diarioextra`, `ncrnoticias`, `elmundo`, `repretel`.
`--feed-file archivo.xml` exige una fuente específica; `--input` solo se admite
en stage/ingest y no se combina con una fuente específica. Para archivos locales
en Docker, añade un montaje `-v /ruta/del/servidor:/ruta/del/contenedor:ro`.

## Servicio en el servidor

```sh
docker compose up -d --build worker
docker compose logs -f worker
docker compose stop worker
```

El worker ejecuta `serve`: comienza inmediatamente, importa el rango configurado,
redacta pendientes, busca fotos para hasta `NEWS_IMAGE_LIMIT` noticias recientes
sin imagen y espera `NEWS_INTERVAL` segundos desde el final del ciclo.
No solapa ciclos. Procesa también pendientes antiguos para reintentar fallos aunque
hayan salido del rango RSS; conserva archivos ya procesados y ediciones manuales.
Un fallo de red o IA se registra y se vuelve a intentar en el siguiente ciclo.
Si todos los RSS fallan, aún se intenta redactar los pendientes locales.
SIGTERM/Ctrl+C detienen la espera o finalizan después de la operación en curso;
ajusta la gracia de Docker por encima de `OLLAMA_TIMEOUT`.

Configura `NEWS_DRAFT=false` para publicar después de la IA. Para cambiar `.env`,
recrea el worker con `docker compose up -d --force-recreate worker`. El servicio
crea Markdown; la compilación y el despliegue de Hugo se ejecutan por separado.
Configura `PEXELS_API_KEY` en `.env` para habilitar imágenes y reconstruye el
worker con `docker compose up -d --build --force-recreate worker`.

La búsqueda usa los temas y, si no existen, el título de la noticia. Agrega
`image_search = false` o `image_requirements = "..."` al front matter de una
noticia para excluirla cuando tenga condiciones editoriales o de derechos
especiales. Las fotos se muestran como ilustrativas; el pie usa la descripción
de Pexels y enlaza la foto, la persona fotógrafa y Pexels. Pexels permite el uso
de sus fotos bajo su licencia, pero no permite presentar personas identificables
de forma ofensiva ni sugerir su respaldo. [Licencia de Pexels](https://www.pexels.com/legal-pages/license),
[guía de la API](https://www.pexels.com/api/documentation/).

## Ramas de revisión desde un servidor

`ingest`, `stage`, `process` y `serve` pueden clonar el repositorio, ejecutar el
pipeline y subir los cambios a una rama para revisión humana. Para GitHub, se
recomienda un **fine-grained personal access token** mediante `GITHUB_TOKEN`;
el código lo envía a Git por HTTPS sin incluirlo en la URL remota ni
escribirlo en el checkout. Configúralo con acceso únicamente a este repositorio,
permiso `Contents: Read and write` y una expiración corta. GitHub incluye el
permiso `Metadata: Read` requerido para acceder al repositorio. [Documentación de
GitHub sobre tokens finos](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens).

```sh
export GITHUB_TOKEN="github_pat_..."
# El worker autentica y sube una rama al final de cada ciclo.
docker compose up -d --build worker
# Alternativa: un único ciclo
GITHUB_TOKEN="$GITHUB_TOKEN" PYTHONPATH=src python3 -m lupita stage \
  --branch ai-editor-stage
```

`--branch` y `--repo` son opcionales. Si falta la rama, se usa `ai-editor-<modelo>`;
`stage` sin modelo usa `ai-editor-stage`. El remoto predeterminado es el `origin`
local o, si no existe, `git@github.com:tavocg/lupita`; con token, se convierte a
HTTPS. `--repo` con token debe ser un remoto de `github.com`.

También sigue disponible `--ssh-key` o `NEWS_SSH_KEY` para autenticación SSH
cuando se necesite. No configures ambas credenciales a la vez. La llave privada,
el token y los archivos de entrada nunca se agregan al repositorio. Compose toma
`GITHUB_TOKEN` del entorno o de `.env`; recuerda que el token debe mantenerse
como secreto. Los tokens finos pueden limitarse a repositorios y permisos
concretos. [Permisos de GitHub para tokens finos](https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens).

Cada ejecución empieza desde el `main` remoto actual y reemplaza la rama de
revisión, incluidos cambios anteriores sin integrar. Nunca escribe ni sube a
`main`, ni modifica el checkout de trabajo. El push usa `--force-with-lease` con
el commit remoto observado al inicio: si otra persona actualiza la rama durante
la ejecución, falla y conserva el clon con los cambios para recuperación.
Si no hay cambios, no crea un commit vacío y deja la rama al nivel de `main`.
Los fallos parciales conservan los borradores y suben los avances, devolviendo
código 1; si el pipeline falla sin cambios, no modifica la rama remota.

`STATE_DIR/git/` conserva las referencias RSS privadas por repositorio y rama,
fuera de los commits. Para separar `stage` y `process`, integra primero la rama
con los borradores en `main` y ejecuta `process` con el mismo `STATE_DIR`, `--repo`
y `--branch` usados en `stage`. Las referencias se conservan incluso después de
procesarlas para poder repetir una revisión todavía no integrada. No se adoptan
los borradores de una rama anterior al reconstruir desde `main`. Para ejecutar
ambas etapas sin integración intermedia, usa `ingest`.

`CONTENT_DIR` debe ser un subdirectorio del repositorio (por defecto `content`);
`STATE_DIR` debe persistir entre ejecuciones. `--branch` y `--repo` requieren
`--ssh-key`/`NEWS_SSH_KEY` o `GITHUB_TOKEN`; la autenticación remota no
se combina con `--dry-run`. En modo SSH, Docker requiere un usuario presente en
`/etc/passwd` y una entrada para el servidor en `known_hosts`.

## Datos y redacción

Los duplicados se detectan por URL normalizada, incluidos borradores y archivos
manuales. Se conservan título, autores y fuente. El contenido RSS es no confiable;
no se publica el cuerpo original. Las fotos de Pexels se buscan automáticamente
solo cuando `PEXELS_API_KEY` está configurada y el worker ejecuta cada ciclo.
`.pipeline/references/` permite reintentar: consérvalo junto con `content/`.
`.news-index.json` contiene referencias completas, es privado y está excluido de Git.

Solo se actualizan borradores intactos del pipeline con `ai_processed=false`.
Las exclusiones eliminan el borrador; los fallos lo conservan. Los archivos antiguos
sin marcador no se procesan. No hay caché de exclusiones; borrar un Markdown permite
reimportarlo. Si se fija un límite, las consultas fallidas y exclusiones lo consumen.
Los registros van a stderr; stdout queda reservado para JSON.

Edita [las instrucciones](src/lupita/editor_instructions.md) o indica un archivo
alternativo en `EDITOR_INSTRUCTIONS_FILE` (móntalo en Docker). El catálogo y las
validaciones permanecen en [editor.py](src/lupita/editor.py). Los temas deben ser
reutilizables; se admite `topics=[]` y nunca se excluye una noticia por longitud.

## Sitio y comprobaciones

El menú del pie usa la referencia de contenido `/archive` y muestra `/archivo/`
en español; enlaza los años con noticias publicadas.
Los listados anuales usan la paginación del sitio. El archivo usa páginas del
idioma actual y enlaces de Hugo; su contenido tiene `translationKey = 'archive'`
y sus etiquetas están en `themes/main/i18n/`. Al añadir un idioma, traduce la
página y su `pageRef` en el menú de ese idioma.

Hugo **0.166.0** y Node.js **22+** para Pagefind **1.5.2**:

```sh
hugo server --buildDrafts                # Vista previa sin índice de búsqueda
sh scripts/build.sh                     # Hugo + Pagefind → public/
sh scripts/build.sh /tmp/lupita-preview --baseURL http://localhost:8080/
python3 -m http.server 8080 --directory /tmp/lupita-preview
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Cloudflare Pages: comando `sh scripts/build.sh`, salida `public`,
`HUGO_VERSION=0.166.0`; configura el dominio en `hugo.toml`. Publica todo el directorio,
incluido `pagefind/`. `/buscar/` indexa títulos y resúmenes publicados y carga sus
recursos al abrir la página. Regenera después de cambiar el contenido.
Referencias: [Hugo](https://gohugo.io/tools/search/) y
[Pagefind](https://pagefind.app/docs/search-ui/).
