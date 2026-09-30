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

Por defecto se consultan todos los medios y se ordenan las noticias por fecha.
Usa `--source` para elegir uno:

| Medio | Valor de `--source` |
| --- | --- |
| La Nación | `nacion` |
| Delfino.cr | `delfino` |
| Semanario Universidad | `semanario` |
| Teletica | `teletica` |
| El Financiero | `elfinanciero` |
| El Observador | `observador` |
| Diario Extra | `diarioextra` |
| NCR Noticias | `ncrnoticias` |
| El Mundo CR (Costa Rica) | `elmundo` |
| Repretel | `repretel` |

Por ejemplo:

```sh
docker compose run --rm --build index --source delfino
```

Teletica combina los feeds de Nacional, Deportes y Emprendedores, sin repetir
noticias con la misma URL normalizada. Para leer un RSS local de cualquiera de esas secciones, usa
`--source teletica --feed-file archivo.xml`.

Si un scraper falla, se registra el error y se continúa con los demás. `scrape`
e `ingest` procesan las noticias disponibles; `index` guarda una instantánea
parcial de los medios que respondieron. Si todos fallan, se conserva el índice
anterior. Un feed vacío cuenta como respuesta válida. El comando devuelve código
1 si hubo errores, incluso cuando pudo procesar otros medios.

`index` reemplaza el índice con todas las notas de los medios seleccionados
(opcional: `--limit 25`). Incluye fecha, título, autores, entradilla, cuerpo y
fuente originales; no filtra por categoría ni por noticias ya publicadas.
El archivo queda en la raíz, excluido de Git. Este modo no requiere configurar Ollama.
Delfino aporta títulos y, cuando existen, entradillas; su RSS no incluye
autores ni cuerpo completo.

Para seleccionar noticias manualmente, edita `.news-index.json` y elimina los
objetos que no quieras procesar, conservando la lista JSON. Puedes guardarla con
otro nombre. Luego monta ese archivo como entrada de solo lectura:

```sh
# Consultar Ollama sin guardar artículos
docker compose run --rm --build \
  -v "$PWD/.news-index.json:/data/news.json:ro" \
  ingest --input /data/news.json --dry-run --limit 10
# Redactar y guardar las noticias seleccionadas
docker compose run --rm --build \
  -v "$PWD/.news-index.json:/data/news.json:ro" \
  ingest --input /data/news.json --limit 50
```

`--input` solo admite `ingest` y sustituye la consulta de scrapers. No se combina
con `--feed-file` ni con la selección de un medio. Se valida todo el JSON antes
de consultar Ollama; cada noticia requiere `date` con zona horaria, `title` y
`source` con `name` y `url`. `authors`, `summary` y `body` pueden estar vacíos u
omitirse. Se mantienen la atribución, la detección de duplicados y los filtros
editoriales. Las noticias se ordenan por fecha y el límite sigue siendo 10 por
defecto. El archivo de entrada no se modifica.

No se descartan noticias por longitud. Se omiten duplicados y temas fuera del catálogo de
[src/lupita/editor.py](src/lupita/editor.py). Las exclusiones por tema consumen
el límite de consultas. Revisa los borradores y cambia `draft = false` para
publicarlos; `NEWS_DRAFT=false` desactiva los borradores en futuras importaciones.

El catálogo incluye Deportes y Mercado. La IA exige un vínculo relevante con
Costa Rica, como personas, empresas o instituciones costarricenses, hechos en el
país o efectos directos sobre él. Se incluyen costarricenses en el extranjero;
publicar en un medio local no basta para acreditar ese vínculo. Política,
Ambiente y Mercado admiten noticias estrictamente internacionales. Finanzas
mantiene el requisito de vínculo local. Mercado abarca actividad empresarial,
comercio, precios y competencia; Finanzas abarca dinero, banca e inversiones.
Este criterio se aplica durante la redacción con Ollama, no al extraer el RSS
ni al generar el índice.
Las instrucciones editoriales se editan en
[editor_instructions.md](src/lupita/editor_instructions.md), que Python carga
como mensaje de sistema. El catálogo, el esquema JSON y la validación permanecen
en `editor.py`.

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
