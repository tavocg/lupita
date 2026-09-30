from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from lupita.__main__ import SCRAPERS, main
from lupita.config import Config
from lupita.editor import validate
from lupita.models import canonical_url
from lupita.storage import destination, frontmatter, identity
from lupita.workflow import process, stage
from test_pipeline import article, generated


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = Config('http://localhost:11434', 'test', self.root / 'content', self.root / 'state', 5, True)
        self.item = article()
        self.path = destination(self.config.content_dir, self.item, None)
        self.editor = Mock()
        self.editor.generate.side_effect = lambda item: validate(generated(), item)

    def test_stage_without_model_and_no_duplicates_or_reference_text_in_markdown(self):
        from lupita.news_index import write_index
        source = self.root / 'input.json'
        write_index(source, [self.item, replace(self.item, source_url=self.item.source_url + '?utm_source=rss')])
        with patch('lupita.__main__.Config.from_env', return_value=self.config) as config, \
             patch('lupita.__main__.OllamaEditor') as editor:
            self.assertEqual(main(['stage', '--input', str(source)]), 0)
            config.assert_called_once_with(require_model=False)
            editor.assert_not_called()
        metadata = frontmatter(self.path)
        self.assertIs(metadata['draft'], True)
        self.assertIs(metadata['ai_processed'], False)
        self.assertEqual(metadata['title'], self.item.title)
        self.assertNotIn(self.item.body, self.path.read_text())
        self.assertNotIn(self.item.summary, self.path.read_text())
        self.assertEqual(stage([self.item], self.config)['duplicates'], 1)
        self.assertEqual(len(list(self.config.content_dir.rglob('*.md'))), 1)
        with patch.dict('os.environ', {'OLLAMA_MODEL': ''}):
            self.assertEqual(Config.from_env(require_model=False).model, '')

    def test_success_preserves_identity_and_is_not_processed_twice(self):
        stage([self.item], self.config)
        result = process(self.editor, replace(self.config, draft=False))
        self.assertEqual(result['written'], 1)
        metadata = frontmatter(self.path)
        self.assertTrue(metadata['ai_processed'])
        self.assertFalse(metadata['draft'])
        self.assertEqual(metadata['source']['url'], canonical_url(self.item.source_url))
        self.assertEqual(metadata['title'], self.item.title)
        self.assertIn(generated()['summary'].replace('.', r'\.'), self.path.read_text())
        self.assertEqual(process(self.editor, self.config)['written'], 0)
        self.editor.generate.assert_called_once()
        self.assertEqual(self.editor.generate.call_args.args[0].source_url, canonical_url(self.item.source_url))

    def test_failure_keeps_pending_and_continues_then_retries(self):
        bad = replace(self.item, source_url='https://example.test/bad')
        stage([bad, self.item], self.config)
        def generate(item):
            if item == bad:
                raise ValueError('respuesta inválida')
            return validate(generated(), item)
        self.editor.generate.side_effect = generate
        result = process(self.editor, self.config)
        self.assertEqual((result['written'], result['failed']), (1, 1))
        badpath = destination(self.config.content_dir, bad, None)
        self.assertIs(frontmatter(badpath)['ai_processed'], False)
        self.editor.generate.side_effect = lambda item: validate(generated(), item)
        self.assertEqual(process(self.editor, self.config)['written'], 1)

    def test_exclusion_deletes_only_pending_and_allows_reimport(self):
        stage([self.item], self.config)
        self.editor.generate.side_effect = lambda item: None
        self.assertEqual(process(self.editor, self.config)['excluded'], 1)
        self.assertFalse(self.path.exists())
        self.assertEqual(stage([self.item], self.config)['staged'], 1)

    def test_missing_reference_is_retryable_and_dry_run_changes_nothing(self):
        stage([self.item], self.config)
        before = self.path.read_bytes()
        reference = self.config.state_dir / 'references' / f'{identity(self.item.source_url)}.json'
        original = reference.read_bytes()
        with redirect_stdout(StringIO()) as output:
            self.assertEqual(process(self.editor, self.config, dry_run=True)['previewed'], 1)
        self.assertEqual(json.loads(output.getvalue())['action'], 'update')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(reference.read_bytes(), original)
        reference.unlink()
        self.assertEqual(process(self.editor, self.config)['failed'], 1)
        self.assertEqual(self.path.read_bytes(), before)

    def test_manual_edits_and_edits_during_generation_are_protected(self):
        stage([self.item], self.config)
        original = self.path.read_bytes()
        self.path.write_bytes(original + b'Edicion manual\n')
        self.assertEqual(process(self.editor, self.config)['failed'], 1)
        self.editor.generate.assert_not_called()
        self.path.write_bytes(original)
        def generate(item):
            self.path.write_bytes(original + b'Edicion concurrente\n')
            return None
        self.editor.generate.side_effect = generate
        self.assertEqual(process(self.editor, self.config)['failed'], 1)
        self.assertTrue(self.path.read_bytes().endswith(b'Edicion concurrente\n'))

    def test_process_does_not_fetch_scrapers(self):
        stage([self.item], self.config)
        with patch('lupita.__main__.Config.from_env', return_value=self.config), \
             patch('lupita.__main__.OllamaEditor', return_value=self.editor), \
             patch('lupita.__main__.SCRAPERS', {'bad': Mock(fetch=Mock(side_effect=AssertionError('RSS')))}):
            self.assertEqual(main(['process']), 0)

    def test_failed_replacement_preserves_pending_and_reference(self):
        stage([self.item], self.config)
        original = self.path.read_bytes()
        with patch('lupita.storage.os.replace', side_effect=OSError('disco')):
            self.assertEqual(process(self.editor, self.config)['failed'], 1)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])
        self.assertEqual(process(self.editor, self.config)['written'], 1)
