from contextlib import redirect_stdout
from datetime import timedelta
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lupita.__main__ import main
from lupita.scrapers import semanario


RSS = b'''<rss version="2.0"
xmlns:dc="http://purl.org/dc/elements/1.1/"
xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><item><title>Acuerdo universitario</title>
<link>https://semanariouniversidad.com/pais/acuerdo/?utm_source=rss</link>
<dc:creator>Ana Solis</dc:creator>
<pubDate>Tue, 29 Sep 2026 18:00:00 +0000</pubDate>
<description><![CDATA[<img src="https://example.test/foto.jpg"><p>Breve &amp; claro.</p>]]></description>
<content:encoded><![CDATA[<p>Texto.</p><script>oculto</script>]]></content:encoded>
</item></channel></rss>'''


class SemanarioTests(unittest.TestCase):
    def test_preserves_attribution_and_text(self):
        item, = semanario.parse_feed(RSS)
        self.assertEqual(item.title, "Acuerdo universitario")
        self.assertEqual(item.authors, ["Ana Solis"])
        self.assertEqual(item.summary, "Breve & claro.")
        self.assertEqual(item.body, "Texto.")
        self.assertEqual(item.source_name, "Semanario Universidad")
        self.assertEqual(item.source_url, "https://semanariouniversidad.com/pais/acuerdo")
        self.assertEqual(item.date.utcoffset(), timedelta(0))

    def test_minimal_entry_and_empty_feed(self):
        raw = b'''<rss><channel><item><title>Nota</title>
        <link>https://www.semanariouniversidad.com/pais/nota</link>
        <pubDate>Tue, 29 Sep 2026 18:00:00 +0000</pubDate>
        </item></channel></rss>'''
        item, = semanario.parse_feed(raw)
        self.assertEqual((item.authors, item.summary, item.body), ([], "", ""))
        self.assertEqual(semanario.parse_feed(b'<rss><channel/></rss>'), [])

    def test_rejects_foreign_domain(self):
        with self.assertLogs("lupita.scrapers.semanario", level="WARNING"), self.assertRaises(ValueError):
            semanario.parse_feed(RSS.replace(b'semanariouniversidad.com/', b'semanariouniversidad.com.evil.test/'))

    def test_selected_source_fetches_feed(self):
        with patch("lupita.scrapers.semanario.request", return_value=RSS) as request, \
             patch("lupita.__main__.nacion.fetch") as nacion, \
             patch("lupita.__main__.delfino.fetch") as delfino, \
             redirect_stdout(StringIO()) as output:
            self.assertEqual(main(["scrape", "--source", "semanario", "--from", "all", "--until", "all"]), 0)
        request.assert_called_once_with("https://semanariouniversidad.com/rss")
        nacion.assert_not_called()
        delfino.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())[0]["source"]["name"], "Semanario Universidad")

    def test_local_feed_does_not_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feed.xml"
            path.write_bytes(RSS)
            with patch("lupita.scrapers.semanario.request") as request, redirect_stdout(StringIO()) as output:
                self.assertEqual(main(["scrape", "--source", "semanario", "--feed-file", str(path), "--from", "all", "--until", "all"]), 0)
            request.assert_not_called()
            self.assertEqual(len(json.loads(output.getvalue())), 1)
