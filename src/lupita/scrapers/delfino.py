import logging

from ..http import request
from ..models import Article
from .rss import parse_rss


FEED_URL = "https://delfino.cr/rss"
LOG = logging.getLogger(__name__)


def parse_feed(raw: bytes) -> list[Article]:
    # El RSS actual solo ofrece entradilla, sin autores ni cuerpo completo.
    # Esos campos quedan vacíos salvo que el medio los incluya en el feed.
    return parse_rss(raw, source_name="Delfino.cr", hosts={"delfino.cr", "www.delfino.cr"}, logger=LOG)


def fetch() -> list[Article]:
    return parse_feed(request(FEED_URL))
