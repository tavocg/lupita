import logging

from ..http import request
from ..models import Article
from .rss import parse_rss


FEED_URL = "https://observador.cr/rss"
LOG = logging.getLogger(__name__)


def parse_feed(raw: bytes) -> list[Article]:
    return parse_rss(raw, source_name="El Observador",
                     hosts={"observador.cr", "www.observador.cr"}, logger=LOG)


def fetch() -> list[Article]:
    return parse_feed(request(FEED_URL))
