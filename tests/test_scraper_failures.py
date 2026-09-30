from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from xml.etree.ElementTree import ParseError

from lupita.__main__ import main
from lupita.config import Config
from lupita.http import RequestError
from lupita.models import Article, Editorial


def article(day):
    return Article(datetime(2026, 9, day, tzinfo=timezone.utc), "Noticia local", [],
                   "Breve.", "", "Medio", f"https://example.test/nota-{day}")


class ScraperFailureTests(unittest.TestCase):
    def test_partial_results_in_all_commands(self):
        for command in ("scrape", "index", "ingest"):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config = Config("http://localhost:11434", "test", root / "content", root / "state", 5, True)
                sources = {
                    "first": SimpleNamespace(fetch=Mock(return_value=[article(28)])),
                    "broken": SimpleNamespace(fetch=Mock(side_effect=RequestError("Sin conexión"))),
                    "last": SimpleNamespace(fetch=Mock(return_value=[article(29)])),
                }
                path = root / "index.json"
                path.write_text('[{"previous": true}]')
                args = [command] + (["--output", str(path)] if command == "index" else [])
                editor = Mock()
                editor.generate.return_value = Editorial("Acuerdo municipal", "Se aprobó la propuesta.", "Política", ["Municipalidades"])
                with patch("lupita.__main__.SCRAPERS", sources), \
                     patch("lupita.__main__.Config.from_env", return_value=config), \
                     patch("lupita.__main__.OllamaEditor", return_value=editor), \
                     self.assertLogs("lupita", level="WARNING") as logs, redirect_stdout(StringIO()) as output:
                    self.assertEqual(main([*args, "--from", "all", "--until", "all"]), 1)
                self.assertTrue(any("broken" in line for line in logs.output))
                for source in sources.values():
                    source.fetch.assert_called_once_with()
                expected = [article(29).to_dict(), article(28).to_dict()]
                if command == "scrape":
                    self.assertEqual(json.loads(output.getvalue()), expected)
                elif command == "index":
                    self.assertEqual(json.loads(path.read_text()), expected)
                else:
                    self.assertEqual([c.args[0] for c in editor.generate.call_args_list], [article(29), article(28)])
                    self.assertEqual(len(list(config.content_dir.rglob("*.md"))), 2)

    def test_all_failures_preserve_index_and_skip_editor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.json"
            path.write_text('[{"previous": true}]')
            sources = {
                str(i): SimpleNamespace(fetch=Mock(side_effect=error))
                for i, error in enumerate((RequestError("HTTP 403"), ParseError("XML"), ValueError("RSS"), OSError("Red")))
            }
            with patch("lupita.__main__.SCRAPERS", sources), \
                 patch("lupita.__main__.OllamaEditor") as editor, self.assertLogs("lupita", level="ERROR"):
                self.assertEqual(main(["index", "--output", str(path), "--from", "all", "--until", "all"]), 1)
            for source in sources.values():
                source.fetch.assert_called_once_with()
            editor.assert_not_called()
            self.assertEqual(path.read_text(), '[{"previous": true}]')

    def test_empty_feed_is_success_even_after_an_error(self):
        for failed in (False, True):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "index.json"
                path.write_text('[{"previous": true}]')
                sources = {"empty": SimpleNamespace(fetch=Mock(return_value=[]))}
                if failed:
                    sources = {"broken": SimpleNamespace(fetch=Mock(side_effect=RequestError("Red"))), **sources}
                with patch("lupita.__main__.SCRAPERS", sources), self.assertLogs("lupita"):
                    self.assertEqual(main(["index", "--output", str(path), "--from", "all", "--until", "all"]), int(failed))
                self.assertEqual(json.loads(path.read_text()), [])
