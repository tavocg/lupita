import logging

from ..http import request
from ..models import Article
from .rss import parse_rss, plain_text


FEED_URL = "https://www.nacion.com/rss/"
EXCLUDED_CATEGORIES = frozenset()
LOG = logging.getLogger(__name__)


def parse_feed(raw: bytes) -> list[Article]:
    return parse_rss(raw, source_name="La Nación", hosts={"www.nacion.com"},
                     logger=LOG, excluded_categories=EXCLUDED_CATEGORIES)


def fetch() -> list[Article]:
    return parse_feed(request(FEED_URL))
