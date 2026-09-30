from contextlib import ExitStack, redirect_stdout
from datetime import timedelta
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lupita.__main__ import SCRAPERS, main
from lupita.http import RequestError


SOURCES = [
    [
        "elfinanciero",
        "El Financiero",
        "https://www.elfinancierocr.com/rss",
        "elfinancierocr.com"
    ],
    [
        "observador",
        "El Observador",
        "https://observador.cr/rss",
        "observador.cr"
    ],
    [
        "diarioextra",
        "Diario Extra",
        "https://www.diarioextra.com/rss",
        "diarioextra.com"
    ],
    [
        "ncrnoticias",
        "NCR Noticias",
        "https://ncrnoticias.com/rss",
        "ncrnoticias.com"
    ],
    [
        "elmundo",
        "El Mundo CR",
        "https://elmundo.cr/costa-rica/rss",
        "elmundo.cr"
    ],
    [
        "repretel",
        "Repretel",
        "https://www.repretel.com/rss",
        "repretel.com"
    ]
]


def fixture(host, *, minimal=False):
    optional = "" if minimal else """
    <dc:creator>Ana</dc:creator><dc:creator>Ana</dc:creator>
    <description><![CDATA[<p>Breve &amp; claro.</p>]]></description>
    <content:encoded><![CDATA[<p>Texto.</p><script>oculto</script>]]></content:encoded>"""
    return f"""<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><item>
    <title>Acuerdo local</title><link>https://{host}/nota/?utm_source=rss</link>
    <pubDate>Tue, 29 Sep 2026 18:00:00 +0000</pubDate>{optional}
    </item></channel></rss>""".encode()


class AdditionalScrapersTests(unittest.TestCase):
    def test_attribution_text_dates_and_minimal_entries(self):
        for key, name, url, host in SOURCES:
            for hostname in (host, "www." + host):
                with self.subTest(source=key, host=hostname):
                    scraper = SCRAPERS[key]
                    item, = scraper.parse_feed(fixture(hostname))
                    self.assertEqual(item.source_name, name)
                    self.assertEqual(item.source_url, f"https://{hostname}/nota")
                    self.assertEqual(item.title, "Acuerdo local")
                    self.assertEqual(item.authors, ["Ana"])
                    self.assertEqual(item.summary, "Breve & claro.")
                    self.assertEqual(item.body, "Texto.")
                    self.assertEqual(item.date.utcoffset(), timedelta(0))
                    minimal, = scraper.parse_feed(fixture(hostname, minimal=True))
                    self.assertEqual((minimal.authors, minimal.summary, minimal.body), ([], "", ""))
                    self.assertEqual(scraper.parse_feed(b"<rss><channel/></rss>"), [])

    def test_invalid_feeds_and_foreign_hosts(self):
        for key, name, url, host in SOURCES:
            with self.subTest(source=key):
                scraper = SCRAPERS[key]
                with self.assertLogs(scraper.LOG, level="WARNING"), self.assertRaises(ValueError):
                    scraper.parse_feed(fixture(host + ".evil.test"))
                with self.assertRaises(ValueError):
                    scraper.parse_feed(b"<html/>")

    def test_normalized_duplicates_and_date_order(self):
        for key, name, url, host in SOURCES:
            with self.subTest(source=key):
                raw = fixture(host)
                duplicate = raw.split(b"<item>", 1)[1].split(b"</item>", 1)[0]
                newer = duplicate.replace(b"/nota/", b"/otra/").replace(b"18:00:00", b"19:00:00")
                raw = raw.replace(b"</channel>", b"<item>" + duplicate + b"</item><item>" + newer + b"</item></channel>")
                items = SCRAPERS[key].parse_feed(raw)
                self.assertEqual([item.source_url for item in items], [f"https://{host}/otra", f"https://{host}/nota"])

    def test_selected_source_and_local_feed(self):
        for key, name, url, host in SOURCES:
            with self.subTest(source=key), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                for other_key, scraper in SCRAPERS.items():
                    if other_key != key:
                        stack.enter_context(patch.object(scraper, "fetch", side_effect=AssertionError("Medio no seleccionado")))
                raw = fixture(host)
                with patch.object(SCRAPERS[key], "request", return_value=raw) as request, redirect_stdout(StringIO()) as output:
                    self.assertEqual(main(["scrape", "--source", key, "--from", "all", "--until", "all"]), 0)
                request.assert_called_once_with(url)
                self.assertEqual(json.loads(output.getvalue())[0]["source"]["name"], name)
                path = Path(directory) / "feed.xml"
                path.write_bytes(raw)
                with patch.object(SCRAPERS[key], "request") as request, redirect_stdout(StringIO()) as output:
                    self.assertEqual(main(["scrape", "--source", key, "--feed-file", str(path), "--from", "all", "--until", "all"]), 0)
                request.assert_not_called()
                self.assertEqual(len(json.loads(output.getvalue())), 1)

    def test_default_index_includes_every_source(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            expected = []
            mocks = []
            for key, scraper in SCRAPERS.items():
                config = next((source for source in SOURCES if source[0] == key), None)
                items = scraper.parse_feed(fixture(config[3])) if config else []
                expected.extend(item.to_dict() for item in items)
                mocks.append(stack.enter_context(patch.object(scraper, "fetch", return_value=items)))
            path = Path(directory) / "index.json"
            self.assertEqual(main(["index", "--output", str(path), "--from", "all", "--until", "all"]), 0)
            self.assertEqual(json.loads(path.read_text()), expected)
            for mock in mocks:
                mock.assert_called_once_with()

    def test_http_failure_preserves_index(self):
        for key, name, url, host in SOURCES:
            with self.subTest(source=key), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "index.json"
                path.write_text('[{"previous": true}]')
                with patch.object(SCRAPERS[key], "request", side_effect=RequestError("HTTP 403")), self.assertLogs("lupita", level="ERROR"):
                    self.assertEqual(main(["index", "--source", key, "--output", str(path), "--from", "all", "--until", "all"]), 1)
                self.assertEqual(path.read_text(), '[{"previous": true}]')
