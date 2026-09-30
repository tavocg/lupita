from contextlib import contextmanager
import fcntl
import hashlib
import html
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib
import unicodedata
from zoneinfo import ZoneInfo

from .models import Article, Editorial, canonical_url


def slug(title: str) -> str:
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")[:90].rstrip("-") or "noticia"


def identity(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "+++":
        return {}
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "+++")
    except StopIteration as error:
        raise ValueError(f"Front matter sin cerrar: {path}") from error
    try:
        return tomllib.loads("\n".join(lines[1:end]))
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"Front matter inválido: {path}") from error


def known_urls(content_dir: Path) -> set[str]:
    urls = set()
    for path in content_dir.rglob("*.md"):
        data = frontmatter(path)
        source = data.get("source", {})
        if isinstance(source, dict) and source.get("url"):
            urls.add(canonical_url(source["url"]))
    return urls


@contextmanager
def pipeline_lock(state_dir: Path):
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "run.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Ya hay otra importación en ejecución") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def render(article: Article, editorial: Editorial, *, draft: bool) -> str:
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    date = article.date.astimezone(ZoneInfo("America/Costa_Rica")).isoformat()
    # JSON strings/arrays are valid here as TOML basic strings/arrays.
    metadata = [
        "+++", f"date = {quote(date)}", f"title = {quote(editorial.title)}",
        f"authors = {quote(article.authors)}", f"category = {quote(editorial.category)}",
        f"topics = {quote(editorial.topics)}", f"draft = {str(draft).lower()}",
        "ai_processed = true",
        f"source_id = {quote(identity(article.source_url))}",
        "[source]", f"  name = {quote(article.source_name)}",
        f"  url = {quote(canonical_url(article.source_url))}", "+++",
    ]
    # El cuerpo generado se publica como texto, nunca como HTML o instrucciones Markdown.
    summary = html.escape(editorial.summary, quote=False)
    summary = re.sub(r"([\\`*_{}\[\]()#+.!|~>\-])", r"\\\1", summary)
    return "\n".join(metadata) + "\n\n" + summary + "\n"


def render_pending(article: Article) -> str:
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    date = article.date.astimezone(ZoneInfo("America/Costa_Rica")).isoformat()
    return "\n".join([
        "+++", f"date = {quote(date)}", f"title = {quote(article.title)}",
        f"authors = {quote(article.authors)}", "draft = true", "ai_processed = false",
        f"source_id = {quote(identity(article.source_url))}",
        "[source]", f"  name = {quote(article.source_name)}",
        f"  url = {quote(canonical_url(article.source_url))}", "+++", "",
    ])


def replace_pending(path: Path, expected: bytes, text: str | None) -> None:
    """Actualiza o elimina solo el borrador pendiente leído, bajo pipeline_lock."""
    temporary = None
    try:
        if text is not None:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, path.stat().st_mode & 0o777)
        if path.read_bytes() != expected or frontmatter(path).get("ai_processed") is not False:
            raise ValueError("El borrador cambió durante la redacción; no se modifica")
        if text is None:
            path.unlink()
        else:
            os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def destination(content_dir: Path, article: Article, editorial: Editorial | None) -> Path:
    date = article.date.astimezone(ZoneInfo("America/Costa_Rica"))
    return content_dir / date.strftime("%Y/%m/%d") / f"{slug(article.title)}-{identity(article.source_url)[:12]}.md"


def write_article(path: Path, text: str) -> None:
    """Publica un archivo completo de manera atómica, sin reemplazar otro."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        # link falla si el destino ya existe; ambos archivos están en el mismo volumen.
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
