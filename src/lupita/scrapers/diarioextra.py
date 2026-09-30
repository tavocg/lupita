import logging

from ..http import request
from ..models import Article
from .rss import parse_rss


FEED_URL = "https://www.diarioextra.com/rss"
LOG = logging.getLogger(__name__)


def parse_feed(raw: bytes) -> list[Article]:
    return parse_rss(raw, source_name="Diario Extra",
                     hosts={"diarioextra.com", "www.diarioextra.com"}, logger=LOG)


def fetch() -> list[Article]:
    return parse_feed(request(FEED_URL))
