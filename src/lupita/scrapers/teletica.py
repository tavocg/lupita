import logging

from ..http import request
from ..models import Article
from .rss import parse_rss


FEED_URLS = (
    "https://www.teletica.com/rss/feed/noticias/nacional",
    "https://www.teletica.com/rss/feed/deportes",
    "https://www.teletica.com/rss/feed/estilo-de-vida/emprendedores",
)
EXCLUDED_CATEGORIES = frozenset()
LOG = logging.getLogger(__name__)


def parse_feed(raw: bytes) -> list[Article]:
    return parse_rss(raw, source_name="Teletica",
                     hosts={"teletica.com", "www.teletica.com"}, logger=LOG,
                     excluded_categories=EXCLUDED_CATEGORIES)


def fetch() -> list[Article]:
    articles = {}
    for url in FEED_URLS:
        for article in parse_feed(request(url)):
            # parse_rss ya normaliza la URL; conservar la primera aparición.
            articles.setdefault(article.source_url, article)
    return sorted(articles.values(), key=lambda article: article.date, reverse=True)
