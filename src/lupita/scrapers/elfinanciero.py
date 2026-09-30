import logging

from ..http import request
from ..models import Article
from .rss import parse_rss


FEED_URL = "https://www.elfinancierocr.com/rss"
EXCLUDED_CATEGORIES = frozenset()
LOG = logging.getLogger(__name__)


def parse_feed(raw: bytes) -> list[Article]:
    return parse_rss(raw, source_name="El Financiero",
                     hosts={"elfinancierocr.com", "www.elfinancierocr.com"}, logger=LOG,
                     excluded_categories=EXCLUDED_CATEGORIES)


def fetch() -> list[Article]:
    return parse_feed(request(FEED_URL))
