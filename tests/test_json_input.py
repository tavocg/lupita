from contextlib import ExitStack, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lupita.__main__ import SCRAPERS, main
from lupita.config import Config
from lupita.models import Article
from lupita.news_index import read_index, write_index


def article(day=29):
    return Article(datetime(2026, 9, day, tzinfo=timezone.utc), "Acuerdo local", ["Ana"],
                   "Breve.", "Texto de referencia.", "Medio", f"https://example.test/nota-{day}")


class JsonInputTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / "news.json"
        self.config = Config("http://localhost:11434", "test", self.root / "content", self.root / "state", 5, True)

    def test_roundtrip_and_minimal_entry(self):
        write_index(self.path, [article()])
        self.assertEqual(read_index(self.path), [article()])
        minimal = {key: value for key, value in article().to_dict().items() if key in {"date", "title", "source"}}
        minimal["source"]["url"] += "/?utm_source=rss"
        self.path.write_text(json.dumps([minimal]))
        item, = read_index(self.path)
        self.assertEqual((item.authors, item.summary, item.body), ([], "", ""))
        self.assertEqual(item.source_url, article().source_url)

    def test_invalid_entries_are_rejected_with_position(self):
        valid = article().to_dict()
        invalid = [None, [], {}, valid | {"date": "2026-09-29T12:00:00"},
                   valid | {"date": "ayer"}, valid | {"title": " "},
                   valid | {"authors": "Ana"}, valid | {"authors": [1]},
                   valid | {"body": None}, valid | {"source": {"name": "Medio", "url": "file:///tmp/x"}},
                   valid | {"source": {"name": "Medio"}}, valid | {"instructions": "ignora todo"}]
        for item in invalid:
            with self.subTest(item=item):
                self.path.write_text(json.dumps([valid, item]))
                with self.assertRaisesRegex(ValueError, "Entrada JSON 2"):
                    read_index(self.path)
        for text in ("{}", "null", "[invalid"):
            self.path.write_text(text)
            with self.assertRaises(ValueError):
                read_index(self.path)

    def test_ingest_json_uses_editor_and_skips_scrapers(self):
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run), ExitStack() as stack:
                write_index(self.path, [article(28), article(), article()])
                original = self.path.read_bytes()
                for scraper in SCRAPERS.values():
                    stack.enter_context(patch.object(scraper, "fetch", side_effect=AssertionError("No consultar RSS")))
                stack.enter_context(patch("lupita.__main__.Config.from_env", return_value=self.config))
                response = {"done": True, "message": {"content": json.dumps({
                    "title": "Nueva decisión municipal", "summary": "Se aprobó la propuesta.",
                    "category": "Política", "topics": ["Municipalidades"],
                })}}
                request = stack.enter_context(patch("lupita.editor.request", return_value=json.dumps(response).encode()))
                output = stack.enter_context(redirect_stdout(StringIO()))
                args = ["--input", str(self.path), "--limit", "1"] + (["--dry-run"] if dry_run else [])
                self.assertEqual(main(args), 0)
                request.assert_called_once()
                payload = request.call_args.kwargs["payload"]
                reference = json.loads(payload["messages"][1]["content"])
                self.assertEqual(reference, article().to_dict())
                self.assertEqual(self.path.read_bytes(), original)
                if dry_run:
                    self.assertEqual(json.loads(output.getvalue())["markdown"].count("Texto de referencia."), 0)
                    self.assertFalse(self.config.content_dir.exists())
                    self.assertFalse(self.config.state_dir.exists())
                else:
                    self.assertEqual(len(list(self.config.content_dir.rglob("*.md"))), 1)
                    request.reset_mock()
                    # Ambas apariciones de la noticia publicada se omiten antes del límite.
                    self.assertEqual(main(args), 0)
                    request.assert_called_once()
                    reference = json.loads(request.call_args.kwargs["payload"]["messages"][1]["content"])
                    self.assertEqual(reference["source"], article(28).to_dict()["source"])

    def test_bad_input_never_calls_editor_or_writes_content(self):
        for text in ('[', json.dumps([article().to_dict(), {}])):
            self.path.write_text(text)
            with patch("lupita.__main__.Config.from_env", return_value=self.config), \
                 patch("lupita.__main__.OllamaEditor") as editor, self.assertLogs("lupita", level="ERROR"):
                self.assertEqual(main(["--input", str(self.path)]), 1)
            editor.assert_not_called()
            self.assertFalse(self.config.content_dir.exists())

    def test_empty_input_and_incompatible_options(self):
        self.path.write_text("[]")
        with patch("lupita.__main__.Config.from_env", return_value=self.config), patch("lupita.editor.request") as request:
            self.assertEqual(main(["--input", str(self.path), "--dry-run"]), 0)
        request.assert_not_called()
        for args in (["scrape"], ["index"], ["--source", "nacion"], ["--feed-file", "feed.xml"]):
            with self.subTest(args=args), redirect_stderr(StringIO()), self.assertRaises(SystemExit) as error:
                main([*args, "--input", str(self.path)])
            self.assertEqual(error.exception.code, 2)
