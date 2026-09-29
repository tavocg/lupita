"""Contrato compartido por los scrapers y el resto del pipeline."""

from dataclasses import dataclass, field
from datetime import datetime
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def canonical_url(value: str) -> str:
    """Quita fragmentos y rastreo, pero conserva parámetros con significado."""
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("La noticia debe tener una URL HTTP(S) absoluta sin credenciales")
    host = parts.hostname.lower()
    scheme = parts.scheme
    port = parts.port
    if host in {"nacion.com", "www.nacion.com"}:
        host, scheme = "www.nacion.com", "https"
    if port and port not in {80, 443}:
        host = f"{host}:{port}"
    query = sorted(
        (key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in {"fbclid", "gclid", "dclid", "mc_cid", "mc_eid"}
    )
    return urlunsplit((scheme, host, parts.path.rstrip("/") or "/", urlencode(query), ""))


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


@dataclass(frozen=True)
class Article:
    date: datetime
    title: str
    authors: list[str]
    summary: str
    body: str
    source_name: str
    source_url: str

    def __post_init__(self):
        if self.date.tzinfo is None or self.date.utcoffset() is None:
            raise ValueError("La fecha debe incluir zona horaria")
        # Algunos RSS publican solo título y enlace; no descartar por texto ausente.
        if not self.title or not self.source_name:
            raise ValueError("Faltan título o medio de la noticia")
        canonical_url(self.source_url)

    def to_dict(self) -> dict:
        return {
            "date": self.date.isoformat(),
            "title": self.title,
            "authors": self.authors,
            "summary": self.summary,
            "body": self.body,
            "source": {"name": self.source_name, "url": self.source_url},
        }


@dataclass(frozen=True)
class Editorial:
    title: str
    summary: str
    category: str
    topics: list[str] = field(default_factory=list)
