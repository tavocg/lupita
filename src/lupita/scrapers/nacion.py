from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import logging
from xml.etree import ElementTree as ET

from ..http import request
from ..models import Article, canonical_url, clean_text


FEED_URL = "https://www.nacion.com/rss/"
# Mediana de 100 notas del RSS, medida el 2026-09-29: 2287 caracteres.
# Texto limpio del cuerpo (o entradilla si no hay cuerpo), sin título ni HTML.
# Umbral fijo propio de este medio; conservó 50 de las 100 notas de la muestra.
MIN_TEXT_LENGTH = 2287
DC = "{http://purl.org/dc/elements/1.1/}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
LOG = logging.getLogger(__name__)


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"p", "br", "div", "li", "h1", "h2", "h3"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
        if tag in {"p", "div", "li", "h1", "h2", "h3"}:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(value: str) -> str:
    parser = TextParser()
    parser.feed(value)
    return clean_text("".join(parser.parts))


def parse_feed(raw: bytes) -> list[Article]:
    root = ET.fromstring(raw)
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("La Nación no devolvió un feed RSS válido")
    articles = []
    seen = set()
    for position, item in enumerate(root.findall("./channel/item"), 1):
        try:
            url = canonical_url(item.findtext("link", ""))
            if url in seen:
                continue
            if not url.startswith("https://www.nacion.com/"):
                raise ValueError("El enlace no pertenece a La Nación")
            authors = list(dict.fromkeys(
                plain_text(author.text or "")
                for author in item.findall(f"{DC}creator") + item.findall("author")
                if plain_text(author.text or "")
            ))
            article = Article(
                date=parsedate_to_datetime(item.findtext("pubDate", "")),
                title=plain_text(item.findtext("title", "")),
                authors=authors,
                summary=plain_text(item.findtext("description", "")),
                body=plain_text(item.findtext(f"{CONTENT}encoded", "")),
                source_name="La Nación", source_url=url,
            )
            articles.append(article)
            seen.add(url)
        except (ValueError, TypeError, OverflowError) as error:
            LOG.warning("Entrada RSS %d omitida: %s", position, error)
    if root.findall("./channel/item") and not articles:
        raise ValueError("El RSS contiene entradas, pero ninguna es válida")
    eligible = []
    for article in sorted(articles, key=lambda article: article.date, reverse=True):
        length = len(article.body or article.summary)
        if length < MIN_TEXT_LENGTH:
            LOG.info("Noticia omitida por longitud (%s): %d < %d caracteres",
                     article.source_url, length, MIN_TEXT_LENGTH)
            continue
        eligible.append(article)
    return eligible


def fetch() -> list[Article]:
    return parse_feed(request(FEED_URL))
