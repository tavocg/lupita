"""Lectura RSS compartida; conserva notas de cualquier longitud."""

from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import logging
from xml.etree import ElementTree as ET
from urllib.parse import urlsplit

from ..models import Article, canonical_url, clean_text


DC = "{http://purl.org/dc/elements/1.1/}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"


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


def parse_rss(raw: bytes, *, source_name: str, hosts: set[str], logger: logging.Logger) -> list[Article]:
    root = ET.fromstring(raw)
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError(f"{source_name} no devolvió un feed RSS válido")
    articles = []
    seen = set()
    for position, item in enumerate(root.findall("./channel/item"), 1):
        try:
            url = canonical_url(item.findtext("link", ""))
            if url in seen:
                continue
            if urlsplit(url).hostname not in hosts:
                raise ValueError(f"El enlace no pertenece a {source_name}")
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
                source_name=source_name, source_url=url,
            )
            articles.append(article)
            seen.add(url)
        except (ValueError, TypeError, OverflowError) as error:
            logger.warning("Entrada RSS %d omitida: %s", position, error)
    if root.findall("./channel/item") and not articles:
        raise ValueError("El RSS contiene entradas, pero ninguna es válida")
    return sorted(articles, key=lambda article: article.date, reverse=True)

