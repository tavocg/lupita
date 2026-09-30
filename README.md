# Lupita

Noticias costarricenses con Hugo. Importa RSS como borradores en
`content/YYYY/MM/DD/` y procesa los pendientes con Ollama en una etapa posterior.

## Importar noticias

Necesitas Docker Compose. Solo la etapa de redacción requiere Ollama en ejecución
con un modelo instalado.
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
# Crear hasta 500 borradores, sin IA
docker compose run --rm --build ingest stage --limit 500
# Procesar hasta 50 pendientes, sin volver a consultar los scrapers
docker compose run --rm --build ingest process --limit 50
# Previsualizar el procesamiento de un pendiente, sin modificarlo
docker compose run --rm --build ingest process --dry-run --limit 1
```

`stage` importa todas las noticias disponibles por defecto. Guarda título, fecha,
autores, fuente, `draft = true` y `ai_processed = false`; el cuerpo del Markdown
queda vacío. Puedes subir estos borradores al repositorio inmediatamente. Hugo
los muestra con `--buildDrafts`; la compilación normal omite los borradores.
La URL normalizada evita repetir noticias, incluso si ya existen como borrador,
artículo procesado o archivo manual.

La referencia completa del RSS se guarda en `.pipeline/references/`, excluida de
Git. Conserva ese directorio para procesar los borradores después, o transfiérelo
de forma privada si vas a ejecutar la IA en otro equipo. El cuerpo original no
se incluye en los Markdown ni se publica. El montaje de `.pipeline` en Compose
permite que ambas etapas compartan las referencias entre contenedores.

`process` consulta únicamente borradores con `ai_processed = false`. Si la IA
los excluye, elimina su Markdown. Si redacta y clasifica correctamente, actualiza
el mismo archivo con el resumen, categoría, temas y `ai_processed = true`.
Conserva el título original. La publicación se controla con `NEWS_DRAFT`
(por defecto `true`): usa `NEWS_DRAFT=false` para publicar los procesados.
Un fallo deja el borrador pendiente y se continúa con los demás. Repetir
`process` reintenta pendientes y omite los completados. Por defecto consulta
10 noticias; las exclusiones y fallos también consumen el límite.

Los archivos antiguos sin marcador y los documentos manuales no se procesan.
Si se editó manualmente un borrador pendiente, se informa del conflicto y se
conserva; también se comprueba que no cambió durante la generación. No hay caché
persistente de exclusiones: al eliminar un Markdown, una importación posterior
puede volver a crearlo si la fuente lo sigue ofreciendo.

`ingest` continúa disponible como atajo que primero crea los borradores del lote
y después los procesa. Para separar las etapas usa `stage` y `process`:

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
# Guardar el índice seleccionado como borradores, sin IA
docker compose run --rm --build \
  -v "$PWD/.news-index.json:/data/news.json:ro" \
  ingest stage --input /data/news.json
```

`--input` admite `stage` e `ingest` y sustituye la consulta de scrapers. No se combina
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

Con Hugo **0.166.0** y Node.js **22+** (incluye npm):

```sh
hugo server --buildDrafts
sh scripts/build.sh
```

En Cloudflare Pages usa `sh scripts/build.sh`, directorio de salida `public`
y variable `HUGO_VERSION=0.166.0`. Configura el dominio definitivo en
`baseURL` de `hugo.toml`. La importación se ejecuta por separado: sube los
Markdown generados al repositorio para incluirlos en el despliegue.

El script ejecuta Hugo y después Pagefind **1.5.2**, descargado mediante `npx`.
Publica todo el directorio de salida, incluido `pagefind/`. La búsqueda en
`/buscar/` funciona en el navegador y carga sus recursos solo en esa página.
Indexa títulos y resúmenes de noticias publicadas; excluye borradores, menús,
portada y listados de categorías o temas. No utiliza `.news-index.json`.

Para probar la búsqueda local con el índice generado:

```sh
sh scripts/build.sh /tmp/lupita-preview --baseURL http://localhost:8080/
python3 -m http.server 8080 --directory /tmp/lupita-preview
```

Abre `http://localhost:8080/buscar/`. Repite la compilación al cambiar noticias.
`hugo server` por sí solo no genera el índice de Pagefind.

Referencias: [búsqueda en Hugo](https://gohugo.io/tools/search/),
[indexación de Pagefind](https://pagefind.app/docs/indexing/) y
[interfaz de Pagefind](https://pagefind.app/docs/search-ui/).
