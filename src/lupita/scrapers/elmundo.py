import logging

from ..http import request
from ..models import Article
from .rss import parse_rss


FEED_URL = "https://elmundo.cr/costa-rica/rss"
EXCLUDED_CATEGORIES = frozenset()
LOG = logging.getLogger(__name__)


def parse_feed(raw: bytes) -> list[Article]:
    return parse_rss(raw, source_name="El Mundo CR",
                     hosts={"elmundo.cr", "www.elmundo.cr"}, logger=LOG,
                     excluded_categories=EXCLUDED_CATEGORIES)


def fetch() -> list[Article]:
    return parse_feed(request(FEED_URL))
