# Lupita

Agregador de noticias costarricenses con Hugo. El importador en `src/lupita/`
lee RSS, solicita una redacción propia a Ollama y escribe Markdown en
`content/YYYY/MM/DD/`. No inicia ni compila Hugo.

## Ejecutar la importación

Necesitas Docker con Compose y un servidor Ollama accesible que ya tenga un
modelo instalado. La configuración de Docker usa la red del host: funciona en
Linux; en Docker Desktop debes habilitar [host networking](https://docs.docker.com/engine/network/drivers/host/).

Preparación inicial:

```sh
cp .env.example .env
mkdir -p .pipeline
```

Edita `.env`: configura `OLLAMA_BASE_URL` y `OLLAMA_MODEL` (nombre exacto que
aparece en `ollama list` en tu servidor). Ajusta `LOCAL_UID` y `LOCAL_GID` con
los resultados de `id -u` e `id -g` para que los archivos pertenezcan a tu usuario.
Las variables del entorno prevalecen sobre `.env` cuando ejecutas Python localmente;
en Docker, el servicio recibe las variables de `env_file`.

Después, una ejecución completa requiere un solo comando:

```sh
docker compose run --rm --build ingest
```

Procesa hasta 10 noticias nuevas por ejecución. Para cambiar el máximo:

```sh
docker compose run --rm --build ingest --limit 25
```

`NEWS_DRAFT=true` genera borradores para revisión. Tras revisarlos, cambia su
`draft` a `false`; para que las futuras importaciones se publiquen directamente,
usa `NEWS_DRAFT=false`. Los borradores también cuentan como ya importados.

No se inicia Ollama ni se descarga un modelo automáticamente. `OLLAMA_BASE_URL`
puede apuntar a otra máquina. El servidor debe admitir `/api/chat` con
[salidas estructuradas](https://docs.ollama.com/capabilities/structured-outputs/).

## Inspeccionar sin escribir noticias

Ver los registros JSON del scraper, sin llamar a Ollama:

```sh
docker compose run --rm --build ingest scrape --limit 3
```

Generar una vista previa con Ollama, sin crear Markdown ni marcar URLs como importadas:

```sh
docker compose run --rm --build ingest --dry-run --limit 1
```

La vista previa imprime un objeto JSON por noticia con `path` y `markdown`.
Los mensajes de progreso se escriben en stderr, para poder redirigir el JSON.

Alternativa local, con Python 3.11+ y sin dependencias adicionales (Linux/macOS):

```sh
PYTHONPATH=src python3 -m lupita
```

El comando local lee `.env` automáticamente. Ejecuta los comandos desde la raíz
del repositorio. `CONTENT_DIR` y `STATE_DIR` permiten elegir directorios alternativos;
Docker fija ambos a sus volúmenes. El lector local de `.env` admite `KEY=value`,
comillas simples/dobles y comentarios; no expande variables ni ejecuta comandos.

## Formato de los scrapers

Cada scraper produce registros con este contrato, definido en `models.Article`:

```json
{
  "date": "2026-09-27T05:15:00-06:00",
  "title": "Título de la fuente",
  "authors": ["Nombre del autor"],
  "summary": "Entradilla de la fuente",
  "body": "Cuerpo de la fuente disponible en el RSS",
  "source": {
    "name": "La Nación",
    "url": "https://www.nacion.com/seccion/noticia/ID/story"
  }
}
```

`date` incluye zona horaria; `authors` puede estar vacío; se necesita `summary`
o `body`. Los scrapers solo extraen estos datos. No clasifican, invocan IA ni
escriben noticias. Para incorporar medios, implementa un módulo en
`src/lupita/scrapers/` que devuelva `list[Article]` y selecciónalo en el comando.

## Redacción y clasificación

Ollama recibe el registro como material de referencia y devuelve un JSON validado
con `title`, `summary`, `category` y `topics`. El título también se redacta de nuevo.
El catálogo de categorías está en `src/lupita/editor.py`; admite una categoría
por noticia, conforme a la taxonomía singular `category` del sitio. Los temas son
de libre elección (1–5), por lo que conviene revisar posibles sinónimos.

El resumen se limita a 130 palabras. Se rechazan respuestas incompletas, temas
repetidos, categorías desconocidas y secuencias literales de 12 palabras presentes
en la fuente. La redacción automática y este control no garantizan exactitud
factual ni cumplimiento de derechos de autor: los borradores permiten revisión
editorial. El cuerpo original solo se utiliza como entrada de Ollama y nunca se
guarda en los archivos Hugo. Tampoco se importan fotografías de terceros.

Las fechas se convierten a `America/Costa_Rica`. Se conservan los autores originales
y `source.name`/`source.url`, para que las plantillas muestren la atribución y el
enlace a la fuente. El cuerpo del Markdown contiene exclusivamente el resumen
generado, escapado para que no introduzca HTML o enlaces arbitrarios.

## Duplicados y fallos

- Se normalizan las URLs: fragmentos y parámetros conocidos de seguimiento se
  descartan; los parámetros con posible significado se conservan. Las variantes
  HTTP/HTTPS y con/sin `www` de La Nación comparten identidad.
- Se consulta `source.url` en los Markdown existentes, incluidos los creados a
  mano. No hace falta una base de datos adicional. El contenido debe utilizar
  front matter TOML, como el resto del repositorio.
- El nombre incluye un slug y 12 caracteres del SHA-256 de la URL. También se
  guarda el hash completo como `source_id`. Noticias con el mismo título y URLs
  distintas no sobrescriben sus archivos.
- Un bloqueo en `.pipeline/run.lock` impide dos importaciones simultáneas. Un
  archivo temporal y una operación atómica evitan dejar Markdown a medio escribir.
  No se sobrescriben archivos ni se regeneran noticias ya importadas.
- Los duplicados se descartan antes de invocar Ollama. Los errores por noticia
  se registran y el lote continúa, con código de salida 1 si hubo fallos. Se
  reintentan en la próxima ejecución porque no se marca la URL como completada.
- La identidad es por URL: dos medios que cuentan el mismo hecho seguirán siendo
  noticias distintas. Si borras un archivo importado, podrá volver a importarse.

## Pruebas

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Usan RSS ficticio, un servidor Ollama simulado y directorios temporales; no
contactan servicios externos ni escriben en `content/`.

## Commits sugeridos

1. Contrato de datos y scraper: `models.py`, `http.py`, `scrapers/` y sus pruebas.
2. Ollama y persistencia: `editor.py`, `storage.py`, `config.py`, `__main__.py` y sus pruebas.
3. Ejecución y documentación: Dockerfile, Compose, archivos de entorno e ignore y este README.
