"""Repositorios reales locales: sin red, llaves reales ni modificación del checkout."""
from dataclasses import replace
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lupita.__main__ import main
from lupita.config import Config
from lupita.editor import validate
from lupita.git_workflow import git, review_branch, run_remote, validate_branch
from lupita.storage import destination
from lupita.workflow import process, stage
from test_pipeline import article, generated


class GitWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / 'remote.git'
        self.seed = self.root / 'seed'
        git('init', '--bare', '--initial-branch=main', str(self.remote))
        git('init', '--initial-branch=main', str(self.seed))
        git('config', 'user.name', 'Test', cwd=self.seed)
        git('config', 'user.email', 'test@example.test', cwd=self.seed)
        (self.seed / 'README.md').write_text('main\n')
        git('add', '.', cwd=self.seed)
        git('commit', '-m', 'Initial', cwd=self.seed)
        git('remote', 'add', 'origin', str(self.remote), cwd=self.seed)
        git('push', 'origin', 'main', cwd=self.seed)
        self.initial = git('rev-parse', 'main', cwd=self.remote)
        self.key = self.root / 'key with spaces'
        self.key.write_text('fake: local transport only')
        self.args = SimpleNamespace(ssh_key=self.key, branch=None, repo=str(self.remote), command='stage')
        self.config = Config('http://localhost:11434', 'qwen3:4b', Path('content'),
                             self.root / 'state', 5, True)
        self.branch = review_branch(self.config.model)

    def stage(self, args, config):
        return int(bool(stage([article()], config)['failed']))

    def test_stage_commit_private_references_and_reset_from_latest_main(self):
        self.assertEqual(run_remote(self.args, self.config, self.stage), 0)
        self.assertEqual(git('rev-parse', 'main', cwd=self.remote), self.initial)
        first = git('rev-parse', self.branch, cwd=self.remote)
        self.assertEqual(git('rev-parse', first + '^', cwd=self.remote), self.initial)
        tree = git('ls-tree', '-r', '--name-only', self.branch, cwd=self.remote)
        self.assertIn('content/', tree)
        self.assertNotIn('.pipeline', tree)
        self.assertEqual(len(list(self.config.state_dir.rglob('references/*.json'))), 1)
        (self.seed / 'README.md').write_text('new main\n')
        git('commit', '-am', 'Advance main', cwd=self.seed)
        git('push', 'origin', 'main', cwd=self.seed)
        latest = git('rev-parse', 'main', cwd=self.remote)
        self.assertEqual(run_remote(self.args, self.config, self.stage), 0)
        self.assertEqual(git('rev-parse', self.branch + '^', cwd=self.remote), latest)
        self.assertEqual(git('rev-parse', 'main', cwd=self.remote), latest)
        self.assertEqual(list(self.config.state_dir.rglob('checkout-*')), [])

    def test_process_merged_stage_uses_private_reference_and_keeps_completed_main(self):
        self.assertEqual(run_remote(self.args, self.config, self.stage), 0)
        git('fetch', 'origin', self.branch, cwd=self.seed)
        git('merge', '--ff-only', 'FETCH_HEAD', cwd=self.seed)
        git('push', 'origin', 'main', cwd=self.seed)
        staged_main = git('rev-parse', 'main', cwd=self.remote)
        self.args.command = 'process'
        editor = Mock()
        editor.generate.side_effect = lambda item: validate(generated(), item)
        def execute(args, config):
            return int(bool(process(editor, config)['failed']))
        self.assertEqual(run_remote(self.args, self.config, execute), 0)
        path = destination(Path('content'), article(), None).as_posix()
        self.assertIn('ai_processed = true', git('show', f'{self.branch}:{path}', cwd=self.remote))
        self.assertIn('ai_processed = false', git('show', f'main:{path}', cwd=self.remote))
        self.assertEqual(git('rev-parse', 'main', cwd=self.remote), staged_main)
        # Rebuild from unmerged main must remain retryable after a successful push.
        self.assertEqual(run_remote(self.args, self.config, execute), 0)
        self.assertEqual(editor.generate.call_count, 2)
        git('fetch', 'origin', self.branch, cwd=self.seed)
        git('merge', '--ff-only', 'FETCH_HEAD', cwd=self.seed)
        git('push', 'origin', 'main', cwd=self.seed)
        self.assertEqual(run_remote(self.args, self.config, execute), 0)
        self.assertEqual(editor.generate.call_count, 2)
        self.assertEqual(git('rev-parse', self.branch, cwd=self.remote),
                         git('rev-parse', 'main', cwd=self.remote))

    def test_process_exclusion_commits_deletion_but_keeps_main_and_reference(self):
        self.assertEqual(run_remote(self.args, self.config, self.stage), 0)
        git('fetch', 'origin', self.branch, cwd=self.seed)
        git('merge', '--ff-only', 'FETCH_HEAD', cwd=self.seed)
        git('push', 'origin', 'main', cwd=self.seed)
        self.args.command = 'process'
        def execute(args, config):
            totals = process(Mock(generate=Mock(return_value=None)), config)
            self.assertEqual(totals['excluded'], 1)
            return totals['failed']
        self.assertEqual(run_remote(self.args, self.config, execute), 0)
        self.assertNotIn('content/', git('ls-tree', '-r', '--name-only', self.branch, cwd=self.remote))
        self.assertIn('content/', git('ls-tree', '-r', '--name-only', 'main', cwd=self.remote))
        self.assertEqual(len(list(self.config.state_dir.rglob('references/*.json'))), 1)

    def test_lease_rejects_concurrent_update_and_preserves_clone(self):
        self.assertEqual(run_remote(self.args, self.config, self.stage), 0)
        def concurrent(args, config):
            # Simulate a reviewer moving the remote branch during generation.
            git('update-ref', 'refs/heads/' + self.branch, self.initial, cwd=self.remote)
            return self.stage(args, config)
        self.assertEqual(run_remote(self.args, self.config, concurrent), 1)
        self.assertEqual(git('rev-parse', self.branch, cwd=self.remote), self.initial)
        self.assertEqual(git('rev-parse', 'main', cwd=self.remote), self.initial)
        clones = list(self.config.state_dir.rglob('checkout-*'))
        self.assertEqual(len(clones), 1)
        self.assertIn('content/', git('ls-tree', '-r', '--name-only', 'HEAD', cwd=clones[0]))

    def test_no_changes_resets_obsolete_review_branch_without_empty_commit(self):
        self.assertEqual(run_remote(self.args, self.config, self.stage), 0)
        self.assertEqual(run_remote(self.args, self.config, lambda *_: 0), 0)
        self.assertEqual(git('rev-parse', self.branch, cwd=self.remote), self.initial)

    def test_invalid_branches_paths_key_and_symlinks_do_not_execute(self):
        callback = Mock()
        for branch in ['main', 'MAIN', 'refs/heads/main', '../main', '@{-1}', '-bad', 'a:b']:
            with self.subTest(branch=branch):
                self.args.branch = branch
                self.assertEqual(run_remote(self.args, self.config, callback), 1)
        self.args.branch = None
        for path in [Path('.'), Path('../outside'), self.root / 'outside', Path('.git')]:
            self.assertEqual(run_remote(self.args, replace(self.config, content_dir=path), callback), 1)
        self.args.ssh_key = self.root / 'missing'
        self.assertEqual(run_remote(self.args, self.config, callback), 1)
        self.args.ssh_key = self.key
        (self.seed / 'content').symlink_to(self.root, target_is_directory=True)
        git('add', 'content', cwd=self.seed)
        git('commit', '-m', 'Symlink', cwd=self.seed)
        git('push', 'origin', 'main', cwd=self.seed)
        self.assertEqual(run_remote(self.args, self.config, callback), 1)
        callback.assert_not_called()

    def test_cli_rejects_incompatible_options_before_execution(self):
        with patch('lupita.__main__.load_env'), patch.dict(os.environ, {'PATH': os.environ['PATH']}, clear=True), \
             patch('lupita.__main__.execute') as execute:
            for args in [ ['stage', '--branch', 'review'], ['index', '--ssh-key', str(self.key)],
                          ['ingest', '--ssh-key', str(self.key), '--dry-run'] ]:
                with self.assertRaises(SystemExit):
                    main(args)
            execute.assert_not_called()

    def test_pipeline_failure_without_changes_preserves_remote_branch(self):
        self.assertEqual(run_remote(self.args, self.config, self.stage), 0)
        before = git('rev-parse', self.branch, cwd=self.remote)
        self.assertEqual(run_remote(self.args, self.config, lambda *_: 1), 1)
        self.assertEqual(git('rev-parse', self.branch, cwd=self.remote), before)

    def test_cli_remote_stage_preserves_local_input_and_content(self):
        from lupita.news_index import write_index
        source = self.root / 'input.json'
        write_index(source, [article()])
        before = source.read_bytes()
        with patch('lupita.__main__.load_env'), patch.dict(os.environ, {'PATH': os.environ['PATH']}, clear=True), \
             patch('lupita.__main__.Config.from_env', return_value=self.config), \
             patch('lupita.__main__.OllamaEditor') as editor:
            self.assertEqual(main(['stage', '--input', str(source), '--from', 'all',
                '--until', 'all', '--ssh-key', str(self.key), '--repo', str(self.remote),
                '--branch', 'human-review']), 0)
            editor.assert_not_called()
        self.assertEqual(source.read_bytes(), before)
        self.assertIn('content/', git('ls-tree', '-r', '--name-only', 'human-review', cwd=self.remote))
        self.assertEqual(git('status', '--porcelain', cwd=self.seed), '')
        self.assertEqual(git('rev-parse', 'main', cwd=self.remote), self.initial)

    def test_model_branch_is_valid(self):
        self.assertEqual(review_branch('qwen3:4b'), 'ai-editor-qwen3-4b')
        self.assertEqual(review_branch(''), 'ai-editor-stage')
        validate_branch(review_branch('org/model:latest'))
