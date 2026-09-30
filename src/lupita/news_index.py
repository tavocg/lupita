"""Instantánea local de los datos del scraper, sin redacción ni clasificación."""

import json
from datetime import datetime
import os
from pathlib import Path
import tempfile

from .models import Article, canonical_url, clean_text


def read_index(path: Path) -> list[Article]:
    """Lee y valida todo el índice antes de consultar Ollama o escribir artículos."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("El índice JSON debe ser una lista de noticias")
    articles = []
    for position, item in enumerate(data, 1):
        try:
            if not isinstance(item, dict):
                raise ValueError("La noticia debe ser un objeto")
            if set(item) - {"date", "title", "authors", "summary", "body", "source"}:
                raise ValueError("La noticia contiene campos desconocidos")
            source = item.get("source")
            if not isinstance(source, dict) or set(source) != {"name", "url"}:
                raise ValueError("source debe contener name y url")
            texts = {key: item.get(key) for key in ("date", "title")}
            texts.update({key: item.get(key, "") for key in ("summary", "body")})
            if any(not isinstance(value, str) for value in (*texts.values(), *source.values())):
                raise ValueError("Fecha, título, textos y fuente deben ser cadenas")
            authors = item.get("authors", [])
            if not isinstance(authors, list) or any(not isinstance(author, str) for author in authors):
                raise ValueError("authors debe ser una lista de cadenas")
            articles.append(Article(
                date=datetime.fromisoformat(texts["date"]), title=clean_text(texts["title"]),
                authors=list(dict.fromkeys(clean_text(author) for author in authors if clean_text(author))),
                summary=clean_text(texts["summary"]), body=clean_text(texts["body"]),
                source_name=clean_text(source["name"]), source_url=canonical_url(source["url"]),
            ))
        except ValueError as error:
            raise ValueError(f"Entrada JSON {position}: {error}") from error
    return articles


def write_index(path: Path, articles: list[Article]) -> None:
    # Se reemplaza la instantánea completa; nunca se deja JSON parcialmente escrito.
    data = json.dumps([article.to_dict() for article in articles], ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=".news-index-", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
