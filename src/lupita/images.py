"""Búsqueda limitada de imágenes editoriales en la API de Pexels."""

import html
import json
import logging
from pathlib import Path
import re
import tomllib
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from .storage import add_image_metadata, frontmatter, pipeline_lock

LOG = logging.getLogger("lupita")
PEXELS_SEARCH = "https://api.pexels.com/v1/search"


def _escape_markdown(value):
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"([\\`*_{}\[\]()#+.!|~>\-])", r"\\\1", value)
    return html.escape(value, quote=False)


def _eligible(data):
    source = data.get("source")
    if data.get("draft", False) or data.get("ai_processed") is False:
        return False
    if data.get("image") or not isinstance(source, dict) or not source.get("url"):
        return False
    # Se omiten noticias con condiciones editoriales o de derechos explícitas.
    if data.get("image_search") is False:
        return False
    if any(data.get(field) for field in (
        "copyright", "copyright_requirements", "image_requirements", "image_rights", "image_copyright",
    )):
        return False
    return True


def _query(data):
    topics = data.get("topics")
    if isinstance(topics, list):
        terms = [str(topic).strip() for topic in topics[:2] if isinstance(topic, str) and topic.strip()]
        if terms:
            return " ".join(terms)[:100]
    return str(data.get("title", "")).strip()[:100]


def _request_photos(api_key, query, timeout):
    params = urlencode({"query": query, "per_page": 5, "orientation": "landscape", "locale": "es-ES"})
    request = Request(PEXELS_SEARCH + "?" + params, headers={
        "Authorization": api_key,
        "User-Agent": "Lupita/0.1 (Pexels image search)",
    })
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(2 * 1024 * 1024 + 1)
    except HTTPError as error:
        raise RuntimeError(f"Pexels respondió HTTP {error.code}") from error
    except (URLError, TimeoutError, OSError) as error:
        raise RuntimeError(f"No se pudo consultar Pexels: {error}") from error
    if len(body) > 2 * 1024 * 1024:
        raise RuntimeError("La respuesta de Pexels supera 2 MiB")
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError) as error:
        raise RuntimeError("Pexels devolvió JSON inválido") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("photos"), list):
        raise RuntimeError("Pexels devolvió una respuesta sin fotos")
    return payload["photos"]


def _pexels_url(value, host):
    if not isinstance(value, str):
        return False
    parts = urlsplit(value)
    return parts.scheme == "https" and (parts.hostname or "").lower() in host and not parts.username and not parts.password


def _select_photo(photos):
    for photo in photos:
        if not isinstance(photo, dict) or not isinstance(photo.get("src"), dict):
            continue
        src = photo["src"].get("large") or photo["src"].get("large2x") or photo["src"].get("medium")
        if (not _pexels_url(src, {"images.pexels.com"})
                or not _pexels_url(photo.get("url"), {"www.pexels.com", "pexels.com"})
                or not _pexels_url(photo.get("photographer_url"), {"www.pexels.com", "pexels.com"})):
            continue
        alt = photo.get("alt")
        photographer = photo.get("photographer")
        if isinstance(alt, str) and alt.strip() and isinstance(photographer, str) and photographer.strip():
            return src, alt.strip(), photographer.strip(), photo["photographer_url"], photo["url"]
    return None


def search_recent(content_dir: Path, state_dir: Path, api_key: str | None, *, limit=2, timeout=30):
    """Busca fotos para las noticias publicadas más recientes que aún no tienen imagen."""
    totals = {"updated": 0, "not_found": 0, "skipped": 0, "failed": 0}
    if not api_key or not api_key.strip():
        LOG.info("Búsqueda de imágenes omitida: configura PEXELS_API_KEY")
        return totals
    with pipeline_lock(state_dir):
        candidates = []
        for path in content_dir.rglob("*.md"):
            if path.is_symlink():
                continue
            try:
                data = frontmatter(path)
                if _eligible(data):
                    candidates.append((path, data))
            except (OSError, ValueError, KeyError, TypeError) as error:
                LOG.warning("No se pudo revisar %s para imágenes: %s", path, error)
        def published_at(entry):
            value = entry[1].get("date")
            if hasattr(value, "timestamp"):
                return value.timestamp()
            try:
                from datetime import datetime
                return datetime.fromisoformat(str(value)).timestamp()
            except (TypeError, ValueError, OverflowError):
                return 0
        candidates.sort(key=published_at, reverse=True)
        for path, data in candidates[:limit]:
            try:
                expected = path.read_bytes()
                photos = _request_photos(api_key.strip(), _query(data), timeout)
                selected = _select_photo(photos)
                if selected is None:
                    totals["not_found"] += 1
                    LOG.info("Pexels no encontró foto apta para %s", path)
                    continue
                image, alt, photographer, photographer_url, photo_url = selected
                caption = (
                    f"Imagen ilustrativa: {_escape_markdown(alt)}. "
                    f"Foto de [{_escape_markdown(photographer)}]({photographer_url}) "
                    f"vía [Pexels]({photo_url})."
                )
                add_image_metadata(path, expected, image=image, alt=alt, caption=caption)
                totals["updated"] += 1
                LOG.info("Imagen de Pexels asignada: %s", path)
            except (RuntimeError, ValueError, OSError, tomllib.TOMLDecodeError) as error:
                totals["failed"] += 1
                LOG.error("No se pudo buscar imagen para %s: %s", path, error)
    return totals
