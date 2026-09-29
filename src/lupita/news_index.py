"""Instantánea local de los datos del scraper, sin redacción ni clasificación."""

import json
import os
from pathlib import Path
import tempfile

from .models import Article


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
