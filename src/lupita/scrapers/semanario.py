import logging

from ..http import request
from ..models import Article
from .rss import parse_rss


FEED_URL = "https://semanariouniversidad.com/rss"
LOG = logging.getLogger(__name__)


def parse_feed(raw: bytes) -> list[Article]:
    return parse_rss(raw, source_name="Semanario Universidad",
                     hosts={"semanariouniversidad.com", "www.semanariouniversidad.com"}, logger=LOG)


def fetch() -> list[Article]:
    return parse_feed(request(FEED_URL))
