# Lupita

Noticias costarricenses con Hugo. Importa RSS, redacta con Ollama y guarda las
noticias en `content/YYYY/MM/DD/`.

## Importar noticias

Necesitas Docker Compose y Ollama en ejecución con un modelo instalado.
Ejecuta desde la raíz del repositorio:

```sh
cp .env.example .env
mkdir -p .pipeline
```

Configura en `.env` `OLLAMA_BASE_URL`, `OLLAMA_MODEL` y los valores de
`LOCAL_UID` / `LOCAL_GID` obtenidos con `id -u` / `id -g`.
Compose usa la red del host; en Docker Desktop habilita
[host networking](https://docs.docker.com/engine/network/drivers/host/).

```sh
# Vista previa sin guardar noticias
docker compose run --rm --build ingest --dry-run --limit 1
# Importar hasta 10 noticias nuevas
docker compose run --rm --build ingest --limit 10
# Guardar los datos del scraper en .news-index.json, sin Ollama
docker compose run --rm --build index
```

`index` reemplaza el índice con todas las notas que superan el mínimo de longitud
(opcional: `--limit 25`). Incluye fecha, título, autores, entradilla, cuerpo y
fuente originales; no filtra por categoría ni por noticias ya publicadas.
El archivo queda en la raíz, excluido de Git. Este modo no requiere configurar Ollama.

Se omiten duplicados, notas cortas y temas fuera del catálogo de
[src/lupita/editor.py](src/lupita/editor.py). Las exclusiones por tema consumen
el límite de consultas. Revisa los borradores y cambia `draft = false` para
publicarlos; `NEWS_DRAFT=false` desactiva los borradores en futuras importaciones.

## Ver y publicar el sitio

Con Hugo **0.166.0**:

```sh
hugo server --buildDrafts
hugo --minify --gc
```

En Cloudflare Pages usa `hugo --minify --gc`, directorio de salida `public`
y variable `HUGO_VERSION=0.166.0`. Configura el dominio definitivo en
`baseURL` de `hugo.toml`. La importación se ejecuta por separado: sube los
Markdown generados al repositorio para incluirlos en el despliegue.
