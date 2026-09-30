"""Importación de borradores y redacción reanudable, en etapas independientes."""

from contextlib import nullcontext
from dataclasses import replace
import json
import logging

from .editor import CATEGORIES
from .models import canonical_url
from .news_index import read_index, write_index
from .storage import (
    destination, frontmatter, identity, known_urls, pipeline_lock, render,
    render_pending, replace_pending, write_article,
)

LOG = logging.getLogger("lupita")


def stage(articles, config, *, limit=None, stop=None):
    totals = {"staged": 0, "duplicates": 0, "failed": 0}
    with pipeline_lock(config.state_dir):
        seen = known_urls(config.content_dir)
        attempted = set()
        for article in articles:
            if stop is not None and stop.is_set():
                break
            url = canonical_url(article.source_url)
            if url in seen or url in attempted:
                totals["duplicates"] += 1
                continue
            if limit is not None and len(attempted) >= limit:
                break
            attempted.add(url)
            try:
                # La referencia se guarda antes del Markdown. Un fallo nunca deja
                # un borrador sin referencia; una referencia huérfana es reintentable.
                reference = config.state_dir / "references" / f"{identity(url)}.json"
                write_index(reference, [article])
                path = destination(config.content_dir, article, None)
                write_article(path, render_pending(article))
                seen.add(url)
                totals["staged"] += 1
                LOG.info("Borrador creado: %s", path)
            except (ValueError, OSError) as error:
                totals["failed"] += 1
                LOG.error("No se pudo importar %s: %s", url, error)
    return totals


def process(editor, config, *, limit=None, dry_run=False, urls=None, window=None, stop=None):
    totals = {"written": 0, "previewed": 0, "duplicates": 0, "excluded": 0, "failed": 0}
    with nullcontext() if dry_run else pipeline_lock(config.state_dir):
        pending = []
        existing = set()
        for path in sorted(config.content_dir.rglob("*.md")):
            try:
                data = frontmatter(path)
                # Solo documentos creados por stage. Los manuales y los antiguos
                # sin marcador no se adoptan ni se eliminan automáticamente.
                if not data.get("source", {}).get("url"):
                    continue
                url = canonical_url(data["source"]["url"])
                if data.get("ai_processed") is not False:
                    existing.add(url)
                    continue
                if urls is not None and url not in urls:
                    continue
                pending.append((path, url))
            except (ValueError, KeyError, TypeError, OSError) as error:
                totals["failed"] += 1
                LOG.error("No se pudo leer %s: %s", path, error)
        # Los directorios YYYY/MM/DD mantienen primero las noticias más recientes.
        pending.sort(key=lambda entry: str(entry[0]), reverse=True)
        seen = existing
        attempted = 0
        for path, url in pending:
            if stop is not None and stop.is_set():
                break
            if url in seen:
                totals["duplicates"] += 1
                continue
            if limit is not None and attempted >= limit:
                break
            seen.add(url)
            try:
                before = path.read_bytes()
                reference = config.state_dir / "references" / f"{identity(url)}.json"
                articles = read_index(reference)
                if len(articles) != 1 or canonical_url(articles[0].source_url) != url:
                    raise ValueError("La referencia local no corresponde al borrador")
                article = articles[0]
                if window is not None and not window.contains(article.date):
                    continue
                attempted += 1
                if before.decode("utf-8") != render_pending(article):
                    raise ValueError("El borrador fue editado manualmente; se conserva sin procesar")
                editorial = editor.generate(article)
                excluded = editorial is None or editorial.category not in CATEGORIES
                # Conservamos siempre el título de la fuente, incluso con editores alternativos.
                markdown = None if excluded else render(
                    article, replace(editorial, title=article.title), draft=config.draft,
                )
                if dry_run:
                    print(json.dumps({"path": str(path), "action": "exclude" if excluded else "update",
                                      "markdown": markdown}, ensure_ascii=False))
                    totals["excluded" if excluded else "previewed"] += 1
                    continue
                replace_pending(path, before, markdown)
                totals["excluded" if excluded else "written"] += 1
                LOG.info("%s: %s", "Excluida" if excluded else "Procesada", path)
                # No hay caché de exclusiones: borrar el Markdown permite reimportar.
                try:
                    reference.unlink(missing_ok=True)
                except OSError as error:
                    LOG.warning("No se pudo limpiar la referencia %s: %s", reference, error)
            except (ValueError, RuntimeError, OSError) as error:
                totals["failed"] += 1
                LOG.error("Borrador pendiente %s: %s", path, error)
    return totals
