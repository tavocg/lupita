"""Importación de borradores y redacción reanudable, en etapas independientes."""

from contextlib import nullcontext
from dataclasses import replace
import json
import logging
import os
from pathlib import Path

from .editor import CATEGORIES
from .models import canonical_url
from .news_index import read_index, write_index
from .storage import (
    destination, frontmatter, identity, known_urls, pipeline_lock, render,
    render_pending, replace_pending, write_article,
)

LOG = logging.getLogger("lupita")


def stage(articles, config, *, limit=None, stop=None):
    totals = {"staged": 0, "duplicates": 0, "recovered": 0, "failed": 0}
    with pipeline_lock(config.state_dir):
        seen = known_urls(config.content_dir)
        pending = {}
        for path in config.content_dir.rglob("*.md"):
            data = frontmatter(path)
            if data.get("ai_processed") is False and data.get("source", {}).get("url"):
                pending.setdefault(canonical_url(data["source"]["url"]), []).append(path)
        attempted = set()
        for article in articles:
            if stop is not None and stop.is_set():
                break
            url = canonical_url(article.source_url)
            if url in seen or url in attempted:
                totals["duplicates"] += 1
                reference = config.state_dir / "references" / f"{identity(url)}.json"
                try:
                    if not reference.exists() and any(
                        path.read_bytes() == render_pending(article).encode("utf-8")
                        for path in pending.get(url, [])
                    ):
                        write_index(reference, [article])
                        totals["recovered"] += 1
                        LOG.info("Referencia recuperada: %s", reference)
                except (ValueError, OSError) as error:
                    totals["failed"] += 1
                    LOG.error("No se pudo recuperar la referencia de %s: %s", url, error)
                continue
            path = destination(config.content_dir, article, None)
            if os.path.lexists(path):
                totals["duplicates"] += 1
                seen.add(url)
                LOG.warning("Destino ya ocupado; se conserva sin sobrescribir: %s (%s)", path, url)
                continue
            if limit is not None and len(attempted) >= limit:
                break
            attempted.add(url)
            try:
                # La referencia se guarda antes del Markdown. Un fallo nunca deja
                # un borrador sin referencia; una referencia huérfana es reintentable.
                reference = config.state_dir / "references" / f"{identity(url)}.json"
                write_index(reference, [article])
                try:
                    write_article(path, render_pending(article))
                except FileExistsError:
                    # Otro escritor puede crear el destino después de comprobarlo.
                    if not os.path.lexists(path):
                        raise
                    seen.add(url)
                    totals["duplicates"] += 1
                    LOG.warning("Destino ya ocupado; se conserva sin sobrescribir: %s (%s)", path, url)
                    continue
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
        candidates = None
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
                if not reference.exists():
                    # Recuperar referencias borradas desde el índice RSS local,
                    # solo cuando sus metadatos coinciden exactamente con el borrador.
                    index_path = Path(os.getenv("NEWS_INDEX_PATH", ".news-index.json"))
                    articles = []
                    if index_path.exists():
                        if candidates is None:
                            candidates = read_index(index_path)
                        matches = [article for article in candidates
                                   if canonical_url(article.source_url) == url]
                        if len(matches) == 1:
                            if render_pending(matches[0]).encode("utf-8") != before:
                                raise ValueError("El borrador fue editado manualmente; se conserva sin procesar")
                            articles = matches
                            if not dry_run:
                                write_index(reference, articles)
                    if not articles:
                        raise ValueError(
                            f"Falta la referencia {reference}; restaura .pipeline/references "
                            f"o recupera la noticia con stage desde su RSS o un índice local. "
                            f"Índice de recuperación: {index_path}"
                        )
                else:
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
