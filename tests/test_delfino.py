from contextlib import redirect_stdout
from datetime import timedelta
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lupita.__main__ import main
from lupita.http import RequestError
from lupita.scrapers import delfino


RSS = b'''<rss version="2.0" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
<channel><item><title>Acuerdo local</title>
<link>https://delfino.cr/2026/09/acuerdo?utm_source=rss</link>
<description><![CDATA[<p>Breve &amp; claro.</p>]]></description>
<pubDate>Tue, 29 Sep 2026 12:00:00 -0600</pubDate>
<news:news><news:publication><news:name>Delfino.cr</news:name></news:publication></news:news>
</item></channel></rss>'''


class DelfinoTests(unittest.TestCase):
    def test_short_description_missing_author_and_body(self):
        item, = delfino.parse_feed(RSS)
        self.assertEqual(item.title, "Acuerdo local")
        self.assertEqual(item.summary, "Breve & claro.")
        self.assertEqual(item.authors, [])
        self.assertEqual(item.body, "")
        self.assertEqual(item.source_name, "Delfino.cr")
        self.assertEqual(item.source_url, "https://delfino.cr/2026/09/acuerdo")
        self.assertEqual(item.date.utcoffset(), timedelta(hours=-6))

    def test_deduplication_and_optional_author_and_body(self):
        raw = RSS
        item = raw.split(b'<item>', 1)[1].split(b'</item>', 1)[0]
        raw = raw.replace(b'</channel>', b'<item>' + item + b'</item></channel>')
        raw = raw.replace(b'<description>', b'<author>Ana</author><description>')
        raw = raw.replace(b'</description>', b'</description><content:encoded xmlns:content="http://purl.org/rss/1.0/modules/content/"><![CDATA[<p>Texto.</p><script>oculto</script>]]></content:encoded>')
        parsed = delfino.parse_feed(raw)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].authors, ["Ana"])
        self.assertEqual(parsed[0].body, "Texto.")

    def test_title_only_entry_is_preserved_without_inventing_text(self):
        raw = RSS.replace(b'<description><![CDATA[<p>Breve &amp; claro.</p>]]></description>', b'<description/>')
        item, = delfino.parse_feed(raw)
        self.assertEqual(item.title, "Acuerdo local")
        self.assertEqual(item.summary, "")
        self.assertEqual(item.body, "")

    def test_reject_wrong_host_and_non_rss_but_accept_empty_feed(self):
        with self.assertLogs("lupita.scrapers.delfino", level="WARNING"), self.assertRaises(ValueError):
            delfino.parse_feed(RSS.replace(b'https://delfino.cr/', b'https://delfino.cr.evil.test/'))
        with self.assertRaises(ValueError):
            delfino.parse_feed(b'<html/>')
        self.assertEqual(delfino.parse_feed(b'<rss><channel/></rss>'), [])

    def test_fetch_uses_delfino_rss(self):
        with patch("lupita.scrapers.delfino.request", return_value=RSS) as request:
            self.assertEqual(len(delfino.fetch()), 1)
        request.assert_called_once_with("https://delfino.cr/rss")

    def test_local_feed_and_source_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feed.xml"
            path.write_bytes(RSS)
            with redirect_stdout(StringIO()) as output, patch("lupita.__main__.nacion.fetch") as other:
                self.assertEqual(main(["scrape", "--source", "delfino", "--feed-file", str(path)]), 0)
            other.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())[0]["source"]["name"], "Delfino.cr")

    def test_default_index_combines_sources_by_date_and_preserves_on_failure(self):
        from dataclasses import replace
        item, = delfino.parse_feed(RSS)
        older = replace(item, date=item.date - timedelta(days=1), source_name="La Nación",
                        source_url="https://www.nacion.com/nota")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".news-index.json"
            with patch("lupita.__main__.nacion.fetch", return_value=[older]), \
                 patch("lupita.__main__.delfino.fetch", return_value=[item]):
                self.assertEqual(main(["index", "--output", str(path)]), 0)
            expected = path.read_text()
            self.assertEqual(json.loads(expected), [item.to_dict(), older.to_dict()])
            with patch("lupita.__main__.nacion.fetch", return_value=[older]), \
                 patch("lupita.__main__.delfino.fetch", side_effect=RequestError("Sin conexión")), \
                 self.assertLogs("lupita", level="ERROR"):
                self.assertEqual(main(["index", "--output", str(path)]), 1)
            self.assertEqual(path.read_text(), expected)
