from contextlib import redirect_stdout
from datetime import timedelta
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import call, patch

from lupita.__main__ import main
from lupita.http import RequestError
from lupita.scrapers import teletica


RSS = b'''<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><item><title>Acuerdo local</title>
<link>https://www.teletica.com/nacional/acuerdo_123?utm_source=rss</link>
<pubDate>Tue, 29 Sep 2026 18:00:00 GMT</pubDate>
<description>Breve &amp; claro.</description>
<content:encoded><![CDATA[<p>Texto.</p><script>oculto</script>]]></content:encoded>
<enclosure url="https://example.test/foto.jpg" type="image/jpeg"/>
</item></channel></rss>'''
EMPTY = b'<rss><channel/></rss>'


class TeleticaTests(unittest.TestCase):
    def test_text_date_and_attribution(self):
        item, = teletica.parse_feed(RSS)
        self.assertEqual(item.title, "Acuerdo local")
        self.assertEqual(item.summary, "Breve & claro.")
        self.assertEqual(item.body, "Texto.")
        self.assertEqual(item.authors, [])
        self.assertEqual(item.source_name, "Teletica")
        self.assertEqual(item.source_url, "https://www.teletica.com/nacional/acuerdo_123")
        self.assertEqual(item.date.utcoffset(), timedelta(0))
        authored = RSS.replace(b'<title>', b'<author>Ana</author><title>')
        self.assertEqual(teletica.parse_feed(authored)[0].authors, ["Ana"])

    def test_minimal_and_empty_feeds(self):
        raw = b'''<rss><channel><item><title>Nota</title>
        <link>https://teletica.com/deportes/nota_1</link>
        <pubDate>Tue, 29 Sep 2026 18:00:00 GMT</pubDate>
        </item></channel></rss>'''
        item, = teletica.parse_feed(raw)
        self.assertEqual((item.authors, item.summary, item.body), ([], "", ""))
        with patch("lupita.scrapers.teletica.request", return_value=EMPTY):
            self.assertEqual(teletica.fetch(), [])

    def test_rejects_foreign_domain_and_non_rss(self):
        with self.assertLogs("lupita.scrapers.teletica", level="WARNING"), self.assertRaises(ValueError):
            teletica.parse_feed(RSS.replace(b'www.teletica.com/', b'www.teletica.com.evil.test/'))
        with self.assertRaises(ValueError):
            teletica.parse_feed(b'<html/>')

    def test_combines_three_feeds_deduplicates_and_sorts(self):
        duplicate = RSS.replace(b'?utm_source=rss', b'?utm_source=otro')
        newer = RSS.replace(b'nacional/acuerdo_123', b'deportes/nota_456').replace(b'18:00:00', b'19:00:00')
        with patch("lupita.scrapers.teletica.request", side_effect=[RSS, duplicate, newer]) as request:
            items = teletica.fetch()
        self.assertEqual(request.call_args_list, [
            call("https://www.teletica.com/rss/feed/noticias/nacional"),
            call("https://www.teletica.com/rss/feed/deportes"),
            call("https://www.teletica.com/rss/feed/estilo-de-vida/emprendedores"),
        ])
        self.assertEqual([item.source_url for item in items], [
            "https://www.teletica.com/deportes/nota_456",
            "https://www.teletica.com/nacional/acuerdo_123",
        ])

    def test_selected_source_and_local_feed(self):
        with patch("lupita.scrapers.teletica.request", side_effect=[RSS, EMPTY, EMPTY]), \
             redirect_stdout(StringIO()) as output:
            self.assertEqual(main(["scrape", "--source", "teletica", "--from", "all", "--until", "all"]), 0)
        self.assertEqual(json.loads(output.getvalue())[0]["source"]["name"], "Teletica")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feed.xml"
            path.write_bytes(RSS)
            with patch("lupita.scrapers.teletica.request") as request, redirect_stdout(StringIO()) as output:
                self.assertEqual(main(["scrape", "--source", "teletica", "--feed-file", str(path), "--from", "all", "--until", "all"]), 0)
            request.assert_not_called()
            self.assertEqual(len(json.loads(output.getvalue())), 1)

    def test_failure_in_each_feed_preserves_index(self):
        for position in range(3):
            with self.subTest(position=position), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "index.json"
                path.write_text('[{"previous": true}]')
                responses = [RSS] * position + [RequestError("Sin conexión")]
                with patch("lupita.scrapers.teletica.request", side_effect=responses), \
                     self.assertLogs("lupita", level="ERROR"):
                    self.assertEqual(main(["index", "--source", "teletica", "--output", str(path), "--from", "all", "--until", "all"]), 1)
                self.assertEqual(path.read_text(), '[{"previous": true}]')
