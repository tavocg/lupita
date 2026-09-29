from contextlib import redirect_stdout
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import URLError

from lupita.__main__ import main, run
from lupita.config import Config, load_env
from lupita.editor import CATEGORIES, EXCLUDED_CATEGORY, OllamaEditor, validate
from lupita.http import RequestError
from lupita.models import Article, Editorial, canonical_url
from lupita.scrapers.nacion import MIN_TEXT_LENGTH, parse_feed, plain_text
from lupita.storage import destination, frontmatter, known_urls, pipeline_lock, render, write_article


RSS = b'''<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"
xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
<item><title>Proyecto local</title>
<link>https://www.nacion.com/region/proyecto/ID/story/?utm_source=rss</link>
<dc:creator>Ana Perez</dc:creator><dc:creator>Luis Mora</dc:creator>
<pubDate>Mon, 28 Sep 2026 03:00:00 +0000</pubDate>
<description><![CDATA[<p>La comunidad <b>presenta</b> su proyecto.</p>]]></description>
<content:encoded><![CDATA[<p>Primer parrafo.</p><script>alert(1)</script><p>Segundo parrafo.</p>]]></content:encoded>
</item>
<item><title>Duplicada</title>
<link>http://nacion.com/region/proyecto/ID/story#top</link>
<pubDate>Mon, 28 Sep 2026 03:00:00 +0000</pubDate><description>Texto.</description></item>
<item><title>Sin fecha valida</title><link>https://www.nacion.com/otra/</link>
<description>Texto.</description></item>
</channel></rss>'''


def article(url="https://www.nacion.com/noticia/ID/story/"):
    return Article(
        date=datetime(2026, 9, 28, 3, tzinfo=timezone.utc),
        title='Informe sobre el proyecto de la comunidad', authors=['Ana "María" Pérez'],
        summary="Una organización presentó un proyecto para mejorar el acceso a servicios públicos.",
        body=("Los vecinos analizaron las necesidades del cantón durante una sesión municipal. "
              "El informe enumera varias propuestas de infraestructura y un calendario de trabajo. "
              "Las autoridades estudiarán el financiamiento antes de decidir cuáles obras iniciar. "
              "La comunidad continuará participando en la discusión de las prioridades."),
        source_name="La Nación", source_url=url,
    )


def generated():
    return {
        "title": "Vecindario propone mejoras en los servicios",
        "summary": "Un grupo comunal planteó cambios para atender carencias locales. La municipalidad evaluará los recursos disponibles antes de aprobar las obras.",
        "category": "Política", "topics": ["Servicios públicos", "Municipalidades"],
    }


class ScraperTests(unittest.TestCase):
    def test_length_threshold_includes_boundary_and_uses_summary_without_body(self):
        items = []
        for index, (length, body) in enumerate((
            (MIN_TEXT_LENGTH - 1, True), (MIN_TEXT_LENGTH, True),
            (MIN_TEXT_LENGTH + 1, True), (MIN_TEXT_LENGTH, False),
        )):
            text = "x" * length
            content = f"<content:encoded><![CDATA[<p>{text}</p>]]></content:encoded>" if body else ""
            # La entradilla no debe inflar la longitud de un cuerpo corto.
            items.append(f"""<item><title>Nota {index}</title>
                <link>https://www.nacion.com/nota/{index}</link>
                <pubDate>Mon, 28 Sep 2026 03:00:00 +0000</pubDate>
                <description>{text}</description>{content}</item>""")
        raw = ('<rss xmlns:content="http://purl.org/rss/1.0/modules/content/">'
               '<channel>' + ''.join(items) + '</channel></rss>').encode()
        result = parse_feed(raw)
        self.assertEqual([a.title for a in result], ["Nota 1", "Nota 2", "Nota 3"])

    def test_all_short_entries_are_skipped_without_invalid_feed_error(self):
        with self.assertLogs("lupita.scrapers.nacion", level="INFO"):
            self.assertEqual(parse_feed(RSS), [])

    @patch("lupita.scrapers.nacion.MIN_TEXT_LENGTH", 0)
    def test_rss_namespaces_html_dates_authors_and_duplicates(self):
        with self.assertLogs("lupita.scrapers.nacion", level="WARNING"):
            result = parse_feed(RSS)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].authors, ["Ana Perez", "Luis Mora"])
        self.assertEqual(result[0].body, "Primer parrafo. Segundo parrafo.")
        self.assertEqual(result[0].summary, "La comunidad presenta su proyecto.")
        self.assertEqual(result[0].date.utcoffset().total_seconds(), 0)
        self.assertEqual(set(result[0].to_dict()), {"date", "title", "authors", "summary", "body", "source"})

    def test_html_entities_and_text_boundaries(self):
        self.assertEqual(plain_text("<p>Uno &amp; dos.</p><p>Tres.</p>"), "Uno & dos. Tres.")

    def test_reject_non_rss(self):
        with self.assertRaises(ValueError):
            parse_feed(b"<html><body>No RSS</body></html>")

    def test_url_tracking_and_meaningful_query(self):
        self.assertEqual(
            canonical_url("http://nacion.com/a/?utm_source=rss&fbclid=x&b=2&a=1#top"),
            "https://www.nacion.com/a?a=1&b=2",
        )
        self.assertNotEqual(canonical_url("https://www.nacion.com/?id=1"), canonical_url("https://www.nacion.com/?id=2"))


class EditorTests(unittest.TestCase):
    def test_allowed_categories_and_explicit_exclusion(self):
        self.assertEqual(set(CATEGORIES), {
            "Ambiente", "Educación", "Ciencia", "Seguridad", "Tecnología",
            "Inteligencia Artificial", "Finanzas", "Cultura", "Política",
        })
        for category in CATEGORIES:
            with self.subTest(category=category):
                self.assertEqual(validate(generated() | {"category": category}, article()).category, category)
        self.assertIsNone(validate(generated() | {"category": EXCLUDED_CATEGORY}, article()))
        for category in ("Deportes", "Sociedad", "Migración", "Internacionales"):
            with self.subTest(category=category), self.assertRaises(ValueError):
                validate(generated() | {"category": category}, article())

    def test_connection_error_identifies_ollama_and_preserves_cause(self):
        with patch("lupita.http.urlopen", side_effect=URLError(ConnectionRefusedError(111, "Connection refused"))) as connect:
            with self.assertRaisesRegex(RequestError, r"Ollama.*Connection refused"):
                OllamaEditor("http://localhost:11434", "test").generate(article())
        connect.assert_called_once()

    def test_valid_response_and_invalid_variants(self):
        self.assertEqual(validate(generated(), article()).category, "Política")
        for update in (
            {"category": "Inventada"}, {"topics": []}, {"topics": ["Tema", "tema"]},
            {"summary": "muy corto"}, {"title": 123}, {"extra": True},
        ):
            with self.subTest(update=update), self.assertRaises(ValueError):
                validate(generated() | update, article())

    def test_reject_literal_copy(self):
        with self.assertRaisesRegex(ValueError, "12 palabras"):
            validate(generated() | {"summary": article().body[:190]}, article())

    def test_real_http_contract_against_fake_ollama(self):
        captured = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                captured.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                body = json.dumps({"done": True, "message": {"content": json.dumps(generated())}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            editor = OllamaEditor(f"http://127.0.0.1:{server.server_port}", "modelo-prueba", 5)
            result = editor.generate(article())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertEqual(result.title, generated()["title"])
        self.assertEqual(captured[0][0], "/api/chat")
        self.assertFalse(captured[0][1]["stream"])
        self.assertEqual(captured[0][1]["format"]["type"], "object")
        self.assertEqual(json.loads(captured[0][1]["messages"][1]["content"])["source"]["name"], "La Nación")


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = Config("http://localhost:11434", "test", self.root / "content", self.root / "state", 5, True)

    def test_excluded_categories_never_create_files_or_previews(self):
        for dry_run in (False, True):
            for result in (None, Editorial("Deportes", "Resultado deportivo", "Deportes", [])):
                with self.subTest(dry_run=dry_run, result=result):
                    class Editor:
                        def generate(self, item):
                            return result
                    with redirect_stdout(StringIO()) as output:
                        totals = run([article()], Editor(), self.config, limit=1, dry_run=dry_run)
                    self.assertEqual(totals["excluded"], 1)
                    self.assertEqual(totals["failed"], 0)
                    self.assertEqual(totals["written"] + totals["previewed"], 0)
                    self.assertEqual(output.getvalue(), "")
                    self.assertFalse(self.config.content_dir.exists())

    def test_scraper_discards_short_notes_before_calling_editor(self):
        with self.assertLogs("lupita.scrapers.nacion", level="INFO"):
            articles = parse_feed(RSS)
        class Editor:
            def generate(self, item):
                raise AssertionError("No debe invocarse para notas cortas")
        totals = run(articles, Editor(), self.config, limit=10)
        self.assertEqual(totals["failed"], 0)
        self.assertEqual(totals["written"], 0)

    def test_date_timezone_toml_escape_and_safe_body(self):
        item = article()
        editorial = Editorial('Título "con comillas" y tildes', '[texto](https://evil.test) <script>mal</script>', "Sociedad", ["Costa Rica"])
        path = destination(self.config.content_dir, item, editorial)
        write_article(path, render(item, editorial, draft=True))
        self.assertIn("2026/09/27/", str(path))
        metadata = frontmatter(path)
        self.assertEqual(metadata["title"], editorial.title)
        self.assertEqual(metadata["authors"], item.authors)
        self.assertEqual(metadata["date"], "2026-09-27T21:00:00-06:00")
        self.assertTrue(metadata["draft"])
        self.assertIn("&lt;script&gt;", path.read_text())
        self.assertIn(r"\[texto\]", path.read_text())

    def test_skip_existing_handwritten_content_before_model_call(self):
        self.config.content_dir.mkdir()
        (self.config.content_dir / "manual.md").write_text('+++\nauthor="Ana"\n[source]\nurl="http://nacion.com/noticia/ID/story/?utm_source=mail"\n+++\nTexto\n')
        class Editor:
            def generate(self, item):
                raise AssertionError("No debe invocarse para duplicados")
        result = run([article()], Editor(), self.config, limit=10)
        self.assertEqual(result["duplicates"], 1)

    def test_atomic_no_overwrite_and_unique_filenames(self):
        editorial = validate(generated(), article())
        first = destination(self.config.content_dir, article(), editorial)
        second = destination(self.config.content_dir, article("https://www.nacion.com/otra/"), editorial)
        self.assertNotEqual(first, second)
        write_article(first, "original")
        with self.assertRaises(FileExistsError):
            write_article(first, "replacement")
        self.assertEqual(first.read_text(), "original")
        self.assertFalse(list(first.parent.glob("*.tmp")))

    def test_lock_blocks_concurrent_execution(self):
        with pipeline_lock(self.config.state_dir):
            with self.assertRaisesRegex(RuntimeError, "otra importación"):
                with pipeline_lock(self.config.state_dir):
                    self.fail("No debe adquirir el bloqueo")

    def test_preview_failure_recovery_and_idempotence(self):
        class Editor:
            def generate(self, item):
                if "failure" in item.source_url:
                    raise ValueError("invalid output")
                return validate(generated(), item)
        with redirect_stdout(StringIO()) as output:
            result = run([article()], Editor(), self.config, limit=1, dry_run=True)
        self.assertEqual(result["previewed"], 1)
        self.assertIn("markdown", json.loads(output.getvalue()))
        self.assertFalse(self.config.content_dir.exists())
        self.assertFalse(self.config.state_dir.exists())
        with self.assertLogs("lupita", level="ERROR"):
            result = run([article("https://www.nacion.com/failure"), article()], Editor(), self.config, limit=2)
        self.assertEqual(result["written"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(run([article()], Editor(), self.config, limit=1)["duplicates"], 1)
        self.assertNotIn("https://www.nacion.com/failure", known_urls(self.config.content_dir))

    def test_broken_existing_toml_stops_scan(self):
        self.config.content_dir.mkdir()
        (self.config.content_dir / "broken.md").write_text('+++\ntitle = "bad\n+++\n')
        with self.assertRaisesRegex(ValueError, "Front matter inválido"):
            known_urls(self.config.content_dir)


class ConfigTests(unittest.TestCase):
    def test_env_preserves_existing_variables_and_quotes(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"OLLAMA_MODEL": "existing"}, clear=True):
            path = Path(directory) / ".env"
            path.write_text('OLLAMA_MODEL="file"\nOLLAMA_BASE_URL=http://example.test:11434\nNEWS_DRAFT=false\n')
            load_env(path)
            config = Config.from_env()
            self.assertEqual(config.model, "existing")
            self.assertFalse(config.draft)

    def test_model_required(self):
        with patch.dict("os.environ", {}, clear=True), self.assertRaisesRegex(ValueError, "OLLAMA_MODEL"):
            Config.from_env()

    @patch("lupita.scrapers.nacion.MIN_TEXT_LENGTH", 0)
    def test_scrape_command_needs_no_ollama(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(StringIO()) as output:
            path = Path(directory) / "rss.xml"
            path.write_bytes(RSS)
            with self.assertLogs("lupita.scrapers.nacion", level="WARNING"):
                code = main(["scrape", "--feed-file", str(path), "--limit", "1"])
            self.assertEqual(code, 0)
            self.assertEqual(len(json.loads(output.getvalue())), 1)


if __name__ == "__main__":
    unittest.main()
