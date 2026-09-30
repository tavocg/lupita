"""Rangos reales, configuración CLI/entorno y ciclos sin red ni esperas reales."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import os
from pathlib import Path
import signal
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lupita.__main__ import execute, main, serve
from lupita.config import Config
from lupita.date_window import DateWindow, ZONE
from lupita.editor import OllamaEditor, validate
from lupita.models import Article, Editorial
from lupita.news_index import read_index, write_index
from lupita.storage import frontmatter
from lupita.workflow import process, stage

NOW = datetime(2026, 9, 30, 12, tzinfo=ZONE)


def article(instant, name="nota"):
    return Article(instant, "Noticia " + name, [], "Breve.", "", "Medio", "https://example.test/" + name)


class DateWindowTests(unittest.TestCase):
    def test_default_is_previous_calendar_day_in_costa_rica(self):
        window = DateWindow.parse(now=NOW)
        self.assertEqual(window.start, datetime(2026, 9, 29, tzinfo=ZONE))
        self.assertTrue(window.contains(NOW))
        self.assertFalse(window.contains(NOW + timedelta(microseconds=1)))
        self.assertTrue(window.contains(datetime(2026, 9, 29, 6, tzinfo=timezone.utc)))
        self.assertFalse(window.contains(datetime(2026, 9, 29, 5, 59, tzinfo=timezone.utc)))
        midnight_utc = datetime(2026, 10, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(DateWindow.parse(now=midnight_utc).start, window.start)

    def test_day_end_includes_whole_day_and_iso_end_is_inclusive(self):
        window = DateWindow.parse("2026-09-29", "2026-09-29")
        last = datetime(2026, 9, 29, 23, 59, 59, 999999, tzinfo=ZONE)
        self.assertTrue(window.contains(last))
        self.assertFalse(window.contains(last + timedelta(microseconds=1)))
        iso = NOW.isoformat()
        exact = DateWindow.parse(iso, iso)
        self.assertTrue(exact.contains(NOW))
        self.assertFalse(exact.contains(NOW + timedelta(microseconds=1)))

    def test_unbounded_and_invalid_ranges(self):
        self.assertTrue(DateWindow.parse("all", "all").contains(NOW + timedelta(days=100)))
        for start, end in (("2026-10-01", "2026-09-30"), ("2026-02-30", "now"),
                           ("2026-09-29T12:00:00", "now"), ("ayer", "now")):
            with self.subTest(start=start), self.assertRaises(ValueError):
                DateWindow.parse(start, end, now=NOW)


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = Config("http://localhost:11434", "test", self.root / "content", self.root / "state", 5, True)
        self.input = self.root / "input.json"
        self.output = self.root / "output.json"
        self.editor = Mock()
        self.editor.generate.side_effect = lambda item: Editorial(item.title, "Resumen breve.", "Sociedad", [])
        for context in (patch.dict(os.environ, {}, clear=True), patch("lupita.__main__.load_env"),
                        patch("lupita.__main__.Config.from_env", return_value=self.config),
                        patch("lupita.__main__.OllamaEditor", return_value=self.editor)):
            context.start()
            self.addCleanup(context.stop)

    def args(self, command="serve"):
        return SimpleNamespace(command=command, limit=None, date_from="yesterday", until="now", interval=28800,
                               input=None, feed_file=None, source="all", output=self.output, dry_run=False)

    def test_default_window_and_unlimited_index(self):
        selected = [article(NOW - timedelta(minutes=i), str(i)) for i in range(20)]
        feed = [article(NOW + timedelta(days=1), "future"), article(NOW - timedelta(days=3), "old"), *selected]
        with patch("lupita.date_window.datetime", wraps=datetime) as clock, \
             patch("lupita.__main__.SCRAPERS", {"test": SimpleNamespace(fetch=lambda: feed)}):
            clock.now.return_value = NOW
            self.assertEqual(main(["index", "--output", str(self.output)]), 0)
        self.assertEqual(read_index(self.output), selected)
        self.editor.generate.assert_not_called()

    def test_input_filter_before_limit_and_dry_run_without_writes(self):
        selected = article(NOW - timedelta(hours=1))
        write_index(self.input, [article(NOW + timedelta(days=2), "future"), selected,
                                 article(NOW - timedelta(days=10), "old")])
        before = self.input.read_bytes()
        args = ["ingest", "--input", str(self.input), "--from", "2026-09-30", "--until", NOW.isoformat(), "--limit", "1"]
        with redirect_stdout(StringIO()) as output:
            self.assertEqual(main([*args, "--dry-run"]), 0)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.editor.generate.assert_called_once_with(selected)
        self.assertFalse(self.config.content_dir.exists())
        self.assertFalse(self.config.state_dir.exists())
        self.assertEqual(main(args), 0)
        self.assertEqual(len(list(self.config.content_dir.rglob("*.md"))), 1)
        self.assertEqual(self.input.read_bytes(), before)

    def test_cli_overrides_environment_and_environment_controls_command(self):
        feed = [article(NOW, "one"), article(NOW - timedelta(days=1), "two")]
        with patch.dict(os.environ, {"NEWS_COMMAND": "index", "NEWS_SOURCE": "test", "NEWS_LIMIT": "1",
                                    "NEWS_FROM": "all", "NEWS_UNTIL": "all", "NEWS_INDEX_PATH": str(self.output)}), \
             patch("lupita.__main__.SCRAPERS", {"test": SimpleNamespace(fetch=lambda: feed)}):
            self.assertEqual(main([]), 0)
            self.assertEqual(read_index(self.output), feed[:1])
            self.assertEqual(main(["--limit", "0", "--from", "2026-09-30"]), 0)
            self.assertEqual(read_index(self.output), feed[:1])
            self.assertEqual(main(["--limit", "all"]), 0)
            self.assertEqual(read_index(self.output), feed)

    def test_env_file_is_loaded_before_argument_defaults(self):
        from lupita.config import load_env
        envfile = self.root / "settings.env"
        envfile.write_text(f"NEWS_COMMAND=stage\nNEWS_INPUT={self.input}\nNEWS_FROM=all\nNEWS_UNTIL=all\nNEWS_LIMIT=1\n")
        write_index(self.input, [article(NOW, str(i)) for i in range(3)])
        with patch("lupita.__main__.load_env", side_effect=lambda: load_env(envfile)), \
             patch.dict(os.environ, {"NEWS_LIMIT": "0"}):
            self.assertEqual(main([]), 0)
        self.assertEqual(len(list(self.config.content_dir.rglob("*.md"))), 3)

    def test_no_dry_run_overrides_env_and_default_ingest_has_no_query_cap(self):
        write_index(self.input, [article(NOW, str(i)) for i in range(12)])
        with patch.dict(os.environ, {"NEWS_DRY_RUN": "true", "NEWS_FROM": "all", "NEWS_UNTIL": "all"}):
            self.assertEqual(main(["--input", str(self.input), "--no-dry-run"]), 0)
        self.assertEqual(self.editor.generate.call_count, 12)
        self.assertEqual(len(list(self.config.content_dir.rglob("*.md"))), 12)

    def test_invalid_settings_fail_before_fetch(self):
        source = Mock()
        with patch("lupita.__main__.SCRAPERS", {"test": source}):
            for args in (["index", "--from", "bad"], ["index", "--from", "2026-10-02", "--until", "2026-10-01"]):
                self.assertEqual(main(args), 1)
            for settings in ({"NEWS_LIMIT": "-1"}, {"NEWS_LIMIT": "bad"}, {"NEWS_SOURCE": "wrong"},
                             {"NEWS_COMMAND": "wrong"}, {"NEWS_INTERVAL": "0"}, {"NEWS_DRY_RUN": "yes"}):
                with self.subTest(settings=settings), patch.dict(os.environ, settings), redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                    main([])
        source.fetch.assert_not_called()
        self.editor.generate.assert_not_called()

    def test_process_date_filter_and_unlimited_default(self):
        old = article(NOW - timedelta(days=10), "old")
        recent = [article(NOW, str(i)) for i in range(12)]
        stage([old, *recent], self.config)
        self.assertEqual(process(self.editor, self.config, window=DateWindow.parse(now=NOW))["written"], 12)
        self.assertEqual(process(self.editor, self.config)["written"], 1)

    def test_worker_retries_old_pending_even_when_all_scrapers_fail(self):
        old = article(NOW - timedelta(days=10))
        stage([old], self.config)
        source = SimpleNamespace(fetch=Mock(side_effect=OSError("offline")))
        self.editor.generate.side_effect = ValueError("IA no disponible")
        with patch("lupita.__main__.SCRAPERS", {"test": source}):
            self.assertEqual(execute(self.args(), self.config), 1)
            pending = next(self.config.content_dir.rglob("*.md"))
            self.assertFalse(frontmatter(pending)["ai_processed"])
            self.editor.generate.side_effect = lambda a: Editorial(a.title, "Resumen.", "Sociedad", [])
            self.assertEqual(execute(self.args(), self.config), 1)  # RSS todavía falla.
            self.assertTrue(frontmatter(pending)["ai_processed"])
            execute(self.args(), self.config)
            self.assertEqual(self.editor.generate.call_count, 2)

    def test_worker_recalculates_window_across_midnight(self):
        feed = [article(NOW - timedelta(days=1), "yesterday"), article(NOW, "today")]
        args = self.args("scrape")
        with patch("lupita.date_window.datetime", wraps=datetime) as clock, \
             patch("lupita.__main__.SCRAPERS", {"test": SimpleNamespace(fetch=lambda: feed)}):
            clock.now.return_value = NOW
            with redirect_stdout(StringIO()) as output:
                execute(args, self.config)
            self.assertEqual(len(json.loads(output.getvalue())), 2)
            clock.now.return_value = NOW + timedelta(days=1)
            with redirect_stdout(StringIO()) as output:
                execute(args, self.config)
            self.assertEqual(len(json.loads(output.getvalue())), 1)

    def test_worker_waits_retries_and_restores_signal_handlers(self):
        stop = Event()
        waits = []
        handlers = {}
        def register(sig, handler):
            handlers[sig] = handler
            return "previous"
        def wait(seconds):
            waits.append(seconds)
            if len(waits) == 2:
                handlers[signal.SIGTERM](signal.SIGTERM, None)
        with patch("lupita.__main__.Event", return_value=stop), patch.object(stop, "wait", side_effect=wait), \
             patch("lupita.__main__.signal.signal", side_effect=register), \
             patch("lupita.__main__.execute", side_effect=[1, 0]) as cycle:
            self.assertEqual(serve(self.args(), self.config), 0)
        self.assertEqual(cycle.call_count, 2)
        self.assertEqual(waits, [28800, 28800])
        self.assertEqual(handlers[signal.SIGTERM], "previous")
        self.assertEqual(handlers[signal.SIGINT], "previous")

    def test_stop_finishes_current_article_and_preserves_remaining(self):
        items = [article(NOW, str(i)) for i in range(3)]
        stage(items, self.config)
        stop = Event()
        def generate(item):
            stop.set()
            return Editorial(item.title, "Resumen.", "Sociedad", [])
        self.editor.generate.side_effect = generate
        self.assertEqual(process(self.editor, self.config, stop=stop)["written"], 1)
        self.assertEqual(sum(frontmatter(p)["ai_processed"] is False for p in self.config.content_dir.rglob("*.md")), 2)

    def test_editor_empty_topics_and_custom_instructions(self):
        data = {"summary": "Resumen.", "category": "Sociedad", "topics": []}
        self.assertEqual(validate(data, article(NOW)).topics, [])
        path = self.root / "instructions.md"
        path.write_text("Instrucciones del servidor.")
        with patch.dict(os.environ, {"EDITOR_INSTRUCTIONS_FILE": str(path)}):
            editor = OllamaEditor("http://localhost:11434", "test")
            with patch("lupita.editor.request", return_value=json.dumps({"done": True, "message": {"content": json.dumps(data)}}).encode()) as request:
                editor.generate(article(NOW))
            self.assertTrue(request.call_args.kwargs["payload"]["messages"][0]["content"].startswith(path.read_text()))
            path.write_text("")
            with self.assertRaises(ValueError):
                OllamaEditor("http://localhost:11434", "test")
