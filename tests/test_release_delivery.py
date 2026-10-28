import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import Mock, patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('delivery', ROOT / 'scripts/release_delivery.py')
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


class APITransportTests(unittest.TestCase):
    def test_repository_metadata_uses_canonical_api_root(self):
        with patch.object(delivery, 'command', return_value=b'{}') as command:
            self.assertEqual(delivery.api(delivery.APP, ''), {})
        command.assert_called_once_with('gh', 'api', 'repos/' + delivery.APP, optional=False)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.sha = 'a' * 40
        self.producer = 'b' * 40
        self.digest = 'c' * 64
        self.manifest = dict(schema_version=1, tag='v2.15.1', source_commit=self.sha,
                             producer_workflow_sha=self.producer, producer_run_id=123,
                             producer_run_attempt=2, checks=dict.fromkeys(delivery.CHECKS, 'success'),
                             artifacts=[dict(name='hitkeep-analytics-2.15.1.zip', type='assistant-package', sha256=self.digest)])
        self.run = dict(id=123, run_attempt=2, repository=dict(full_name=delivery.APP),
                        head_repository=dict(full_name=delivery.APP), head_branch='main',
                        path=delivery.PRODUCER, head_sha=self.producer, event='workflow_dispatch',
                        status='completed', conclusion='success')
        self.certificate = dict(sourceRepositoryURI='https://github.com/' + delivery.APP,
                                sourceRepositoryDigest=self.producer, sourceRepositoryRef='refs/heads/main',
                                buildSignerURI='https://github.com/' + delivery.APP + '/' + delivery.PRODUCER + '@refs/heads/main',
                                runInvocationURI='https://github.com/' + delivery.APP + '/actions/runs/123/attempts/2')
        self.attestations = [dict(verificationResult=dict(signature=dict(certificate=self.certificate)))]

    def validate(self):
        delivery.validate_candidate(self.manifest, self.run, self.attestations,
                                    'v2.15.1', self.sha, self.digest)

    def test_candidate_producer_and_attempt_are_bound(self):
        self.validate()
        for key, value in [('run_attempt', 1), ('head_sha', 'd' * 40), ('head_branch', 'untrusted'),
                           ('event', 'pull_request'), ('conclusion', 'failure')]:
            with self.subTest(key=key):
                previous = self.run[key]
                self.run[key] = value
                with self.assertRaises(ValueError):
                    self.validate()
                self.run[key] = previous
        self.certificate['runInvocationURI'] = self.certificate['runInvocationURI'].replace('/2', '/1')
        with self.assertRaises(ValueError):
            self.validate()

    def test_wrong_signer_missing_check_and_duplicate_asset_fail(self):
        for mutate in (lambda: self.certificate.update(buildSignerURI='untrusted'),
                       lambda: self.manifest['checks'].pop('native'),
                       lambda: self.manifest['artifacts'].append(copy.deepcopy(self.manifest['artifacts'][0]))):
            self.setUp()
            mutate()
            with self.assertRaises(ValueError):
                self.validate()

    def test_dispatch_identity_is_closed(self):
        env = dict(GITHUB_REPOSITORY=delivery.REPO, GITHUB_REF='refs/heads/main',
                   GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_WORKFLOW_REF=delivery.REPO + '/' + delivery.WORKFLOW + '@refs/heads/main')
        delivery.validate_dispatch(env)
        for key in env:
            invalid = dict(env, **{key: 'untrusted'})
            with self.assertRaises(ValueError):
                delivery.validate_dispatch(invalid)

    def test_private_draft_prerelease_and_moved_tag_fail(self):
        release = dict(tag_name='v2.15.1', draft=False, prerelease=False)
        delivery.validate_release(dict(private=False), release, self.sha, 'v2.15.1', self.sha)
        for repository, changed, tag_sha in ((dict(private=True), release, self.sha),
                                             (dict(private=False), dict(release, draft=True), self.sha),
                                             (dict(private=False), dict(release, prerelease=True), self.sha),
                                             (dict(private=False), release, 'd' * 40)):
            with self.assertRaises(ValueError):
                delivery.validate_release(repository, changed, tag_sha, 'v2.15.1', self.sha)

    def test_zip_faults_do_not_write(self):
        expected = sorted('hitkeep-analytics/' + path for path in delivery.package.CANONICAL | {
            'plugin.json', '.claude-plugin/plugin.json', 'source.json', 'README.md'})
        for fault in ('traversal', 'hook', 'contributor', 'private', 'duplicate', 'symlink', 'executable'):
            with self.subTest(fault=fault):
                names = list(expected)
                if fault == 'traversal': names[0] = '../escape'
                if fault == 'hook': names.append('hitkeep-analytics/hook.sh')
                if fault == 'contributor': names.append('hitkeep-analytics/.agents/skills/private/SKILL.md')
                if fault == 'private': names.append('hitkeep-analytics/private-report.json')
                if fault == 'duplicate': names[0] = names[1]
                raw = io.BytesIO()
                import warnings
                with warnings.catch_warnings(), zipfile.ZipFile(raw, 'w') as archive:
                    warnings.simplefilter('ignore', UserWarning)
                    for index, name in enumerate(names):
                        info = zipfile.ZipInfo(name)
                        mode = 0o120777 if fault == 'symlink' and index == 0 else 0o100644
                        if fault == 'executable' and index == 0: mode = 0o100755
                        info.external_attr = mode << 16
                        archive.writestr(info, 'data')
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp) / 'output'
                    with self.assertRaises(ValueError):
                        delivery.unpack(raw.getvalue(), root)
                    self.assertFalse(root.exists())

    def test_optional_transport_only_accepts_explicit_404(self):
        from subprocess import CompletedProcess
        for code in (401, 403, 500):
            with patch.object(delivery.subprocess, 'run', return_value=CompletedProcess([], 1, b'', f'gh: failed (HTTP {code})'.encode())):
                with self.assertRaises(ValueError):
                    delivery.command('gh', 'api', 'example', optional=True)
        with patch.object(delivery.subprocess, 'run', return_value=CompletedProcess([], 1, b'', b'gh: Not Found (HTTP 404)')):
            self.assertIsNone(delivery.command('gh', 'api', 'example', optional=True))

    def test_policy_and_failed_validation_cannot_reach_writer(self):
        for protected, rules in [(True, []), (False, [dict(enforcement='active')])]:
            with self.assertRaises(ValueError):
                delivery.validate_policy(dict(protected=protected), rules)
        writer = Mock()
        with patch.object(delivery, 'prepare', side_effect=ValueError('network/auth/validation failure')):
            with self.assertRaises(ValueError):
                delivery.deliver('v2.15.1', self.sha, self.digest, ROOT, writer=writer)
        writer.assert_not_called()

    def test_semver_orders_backports_numerically(self):
        self.assertLess(delivery.version('v2.9.9'), delivery.version('v2.15.1'))
        for value in ['v2.015.1', '../escape', '2.15.1', 'v2.15.1-rc.1']:
            with self.assertRaises(ValueError):
                delivery.version(value)


class QueuePolicyTests(unittest.TestCase):
    def test_global_receiver_queue_preserves_multiple_pending_native_releases(self):
        workflow = (ROOT / '.github/workflows/sync-hitkeep-release.yml').read_text()
        concurrency = workflow.split('\nconcurrency:\n', 1)[1].split('\nenv:\n', 1)[0]
        self.assertIn('  group: sync-hitkeep-release\n', concurrency)
        self.assertIn('  cancel-in-progress: false\n', concurrency)
        self.assertIn('  queue: max\n', concurrency)


class WriterTests(unittest.TestCase):
    """Real local Git objects/pushes, fake GitHub APIs; never touches a public repository."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'checkout'
        self.root.mkdir()
        import shutil
        for name in ('plugins', '.claude-plugin', '.agents'):
            shutil.copytree(ROOT / name, self.root / name)
        shutil.copyfile(ROOT / 'LICENSE', self.root / 'LICENSE')
        (self.root / 'reviewed-code').write_text('must not change')
        self.git(self.root, 'init', '-q', '-b', 'main')
        self.git(self.root, 'add', '-f', '.')
        self.git(self.root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'baseline')
        self.remote = self.base / 'remote.git'
        self.git(self.root, 'clone', '-q', '--bare', str(self.root), str(self.remote))
        self.releases = {'v2.15.0': dict(tag_name='v2.15.0', draft=False, prerelease=False, body='historical', assets=[dict(name='preserved.zip')])}
        self.latest = 'v2.15.0'
        self.mutations = []
        self.protected = False
        self.interrupt = None
        self.original_command = delivery.command
        self.addCleanup(patch.stopall)
        patch.object(delivery, 'command', side_effect=self.command).start()
        patch.object(delivery, 'api', side_effect=self.api).start()
        patch.object(delivery, 'tag_commit', side_effect=self.tag).start()
        patch.object(delivery, 'download', side_effect=lambda entry: entry['bytes']).start()
        self.state = self.new_state('v2.15.1')

    def git(self, root, *args):
        import subprocess
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE).decode().strip()

    def head(self):
        return self.git(self.remote, 'rev-parse', 'refs/heads/main')

    def new_state(self, tag):
        files = {name: (self.root / name).read_bytes() for name in delivery.package.GENERATED}
        for path in ('plugins/hitkeep-analytics/plugin.json', 'plugins/hitkeep-analytics/.claude-plugin/plugin.json'):
            value = json.loads(files[path]); value['version'] = tag[1:]
            files[path] = (json.dumps(value, indent=2) + '\n').encode()
        path = '.claude-plugin/marketplace.json'
        value = json.loads(files[path]); value['plugins'][0]['version'] = tag[1:]
        files[path] = (json.dumps(value, indent=2) + '\n').encode()
        return dict(tag=tag, sha='a' * 40, digest=hashlib.sha256(b'zip').hexdigest(), head=self.head(),
                    candidate=b'candidate', tree=files)

    def api(self, repo, path, optional=False):
        if repo == delivery.APP:
            if path == '':
                return dict(private=False)
            if path.startswith('releases/tags/'):
                return dict(tag_name=path.split('/')[-1], draft=False, prerelease=False,
                            assets=[dict(name='release-candidate.json', bytes=b'candidate'),
                                    dict(name='hitkeep-analytics-' + path.split('/')[-1][1:] + '.zip', bytes=b'zip')])
        if path == 'branches/main':
            return dict(protected=self.protected, commit=dict(sha=self.head()))
        if path == 'rulesets?includes_parents=true':
            return []
        if path == 'releases/latest':
            return self.releases[self.latest]
        if path.startswith('releases/tags/'):
            return self.releases.get(path.split('/')[-1])
        raise AssertionError((repo, path))

    def tag(self, repo, tag):
        if repo == delivery.APP:
            return 'a' * 40
        try:
            return self.git(self.remote, 'rev-parse', '--verify', 'refs/tags/' + tag)
        except Exception:
            return None

    def command(self, *args, **kwargs):
        args = list(args)
        if args[:3] == ['gh', 'release', 'create']:
            self.mutations.append('release')
            if self.interrupt == 'before-release':
                self.interrupt = None
                raise ValueError('injected interruption before release creation')
            tag = args[3]
            self.releases[tag] = dict(tag_name=tag, draft=False, prerelease=False,
                                      body=Path(args[args.index('--notes-file') + 1]).read_text(), assets=[])
            if '--latest=true' in args:
                self.latest = tag
            if self.interrupt == 'after-release':
                self.interrupt = None
                raise ValueError('injected disconnect after release creation')
            return b''
        if 'remote' in args and 'set-url' in args:
            args[-1] = str(self.remote)
        if 'push' in args:
            target = args[-1].split(':')[-1]
            if self.interrupt == 'before-tag' and target.startswith('refs/tags/'):
                self.interrupt = None
                raise ValueError('injected interruption after main update')
            self.mutations.append(target)
            self.assertNotIn('--force', args)
            self.assertNotIn('--force-with-lease', args)
        return self.original_command(*args, **kwargs)

    def write(self, state=None):
        directory = self.base / ('attempt-' + str(len(list(self.base.glob('attempt-*')))))
        directory.mkdir()
        return delivery.write(state or self.state, self.root, directory)

    def generated_commit(self, files=None, extras=None, modes=None):
        import os
        import subprocess
        with tempfile.TemporaryDirectory(dir=self.base) as directory:
            env = dict(os.environ, GIT_INDEX_FILE=str(Path(directory) / 'index'))
            def git(*args, data=None):
                return subprocess.check_output(['git', '-C', str(self.root), *args], input=data,
                                               env=env, stderr=subprocess.PIPE).decode().strip()
            parent = self.git(self.root, 'rev-parse', 'HEAD')
            git('read-tree', parent)
            for path, data in {**(files or {}), **(extras or {})}.items():
                blob = git('hash-object', '-w', '--stdin', data=data)
                git('update-index', '--add', '--cacheinfo', (modes or {}).get(path, '100644') + ',' + blob + ',' + path)
            for path, mode in (modes or {}).items():
                if path not in (files or {}) and path not in (extras or {}):
                    blob = git('rev-parse', parent + ':' + path)
                    git('update-index', '--cacheinfo', mode + ',' + blob + ',' + path)
            tree = git('write-tree')
            return git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                       'commit-tree', tree, '-p', parent, '-m', 'generated tree fixture')

    def assert_generated_refused(self, ref, *, extras=None, modes=None, files=None):
        commit = self.generated_commit(files=self.state['tree'] if ref.startswith('refs/tags/') else files,
                                       extras=extras, modes=modes)
        self.git(self.root, 'push', str(self.remote), commit + ':' + ref)
        if ref == 'refs/heads/main':
            self.state['head'] = commit
        before_main = self.head()
        before_refs = self.git(self.remote, 'show-ref')
        with self.assertRaises(ValueError):
            self.write()
        self.assertEqual(self.mutations, [])
        self.assertEqual(self.head(), before_main)
        self.assertEqual(self.git(self.remote, 'show-ref'), before_refs)
        self.assertEqual(self.releases['v2.15.0']['assets'], [dict(name='preserved.zip')])

    def test_existing_tag_rejects_extra_plugin_hook_even_when_expected_bytes_match(self):
        self.assert_generated_refused('refs/tags/v2.15.1',
            extras={'plugins/hitkeep-analytics/hooks/hooks.json': b'{"hooks":{}}'})

    def test_current_main_rejects_extra_plugin_file_instead_of_carrying_it_forward(self):
        self.assert_generated_refused('refs/heads/main',
            extras={'plugins/hitkeep-analytics/private.txt': b'not attested'})

    def test_existing_tag_rejects_extra_claude_catalog_content(self):
        self.assert_generated_refused('refs/tags/v2.15.1',
            extras={'.claude-plugin/hooks/hooks.json': b'{"hooks":{}}'})

    def test_current_main_rejects_extra_openai_catalog_content(self):
        self.assert_generated_refused('refs/heads/main',
            extras={'.agents/plugins/extra.json': b'{}'})

    def test_existing_tag_rejects_executable_generated_file(self):
        self.assert_generated_refused('refs/tags/v2.15.1',
            modes={'plugins/hitkeep-analytics/skills/hitkeep-analytics/SKILL.md': '100755'})

    def test_current_main_rejects_symlink_mode_with_matching_catalog_bytes(self):
        self.assert_generated_refused('refs/heads/main', files=self.state['tree'],
            modes={'.agents/plugins/marketplace.json': '120000'})

    def test_current_main_rejects_unexpected_native_catalog_fields(self):
        path = '.claude-plugin/marketplace.json'
        data = json.loads((self.root / path).read_bytes())
        data['hooks'] = {'unexpected': 'not attested'}
        self.assert_generated_refused('refs/heads/main', files={path: json.dumps(data).encode()})

    def test_complete_matching_tag_is_noop_and_repository_owned_files_are_preserved(self):
        owned = {'scripts/owned.py': b'raise RuntimeError("never execute")',
                 '.github/workflows/owned.yml': b'name: preserved', 'README.md': b'repository README',
                 '.agents/skills/repository-owned/SKILL.md': b'not a generated product skill'}
        commit = self.generated_commit(files=self.state['tree'], extras=owned)
        self.git(self.root, 'push', str(self.remote), commit + ':refs/heads/main', commit + ':refs/tags/v2.15.1')
        self.state['head'] = commit
        self.latest = 'v2.15.1'
        self.releases[self.latest] = dict(tag_name=self.latest, draft=False, prerelease=False,
                                         body=delivery.release_notes(self.state), assets=[])
        self.assertEqual(self.write(), 'main-and-tag')
        self.assertEqual(self.mutations, [])
        for path, data in owned.items():
            self.assertEqual(delivery.package.git(self.remote, 'show', commit + ':' + path), data)

    def test_newest_fast_forwards_only_generated_files_and_retry_is_noop(self):
        old = self.head()
        self.assertEqual(self.write(), 'main-and-tag')
        new = self.head()
        self.assertNotEqual(new, old)
        self.assertEqual(self.git(self.remote, 'show', new + ':reviewed-code'), 'must not change')
        changed = self.git(self.remote, 'diff', '--name-only', old, new).splitlines()
        self.assertTrue(set(changed) <= delivery.package.GENERATED)
        self.assertEqual(self.tag(delivery.REPO, 'v2.15.1'), new)
        self.assertEqual(self.releases['v2.15.1']['assets'], [])
        self.assertIn('/HitKeep/hitkeep/releases/download/v2.15.1/', self.releases['v2.15.1']['body'])
        previous = list(self.mutations)
        self.assertEqual(self.write(), 'main-and-tag')
        self.assertEqual(previous, self.mutations)
        self.assertEqual(self.releases['v2.15.0']['assets'], [dict(name='preserved.zip')])

    def test_backport_creates_only_historical_tag_and_link(self):
        self.write()
        head = self.head()
        state = self.new_state('v2.14.9')
        self.assertEqual(self.write(state), 'historical-tag-only')
        self.assertEqual(self.head(), head)
        self.assertEqual(self.latest, 'v2.15.1')
        self.assertNotEqual(self.tag(delivery.REPO, 'v2.14.9'), head)
        self.assertEqual(self.git(self.remote, 'for-each-ref', '--format=%(refname)', 'refs/heads'), 'refs/heads/main')

    def test_immutable_tag_and_release_conflicts_fail_before_writes(self):
        self.git(self.remote, 'update-ref', 'refs/tags/v2.15.1', self.head())
        with self.assertRaisesRegex(ValueError, 'immutable assistant tag'):
            self.write()
        self.assertEqual(self.mutations, [])
        self.git(self.remote, 'update-ref', '-d', 'refs/tags/v2.15.1')
        self.releases['v2.15.1'] = dict(tag_name='v2.15.1', draft=False, prerelease=False, body='conflict', assets=[])
        with self.assertRaisesRegex(ValueError, 'immutable assistant release'):
            self.write()
        self.assertEqual(self.mutations, [])

    def test_protection_and_stale_head_fail_before_writes(self):
        self.protected = True
        with self.assertRaisesRegex(ValueError, 'policy changed'):
            self.write()
        self.assertEqual(self.mutations, [])
        self.protected = False
        old = self.head()
        tree = self.git(self.remote, 'rev-parse', old + '^{tree}')
        racing = self.git(self.remote, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                          'commit-tree', tree, '-p', old, '-m', 'concurrent head')
        self.git(self.remote, 'update-ref', 'refs/heads/main', racing)
        with self.assertRaisesRegex(ValueError, 'main changed'):
            self.write()
        self.assertEqual(self.mutations, [])

    def test_native_retry_resumes_each_interruption(self):
        for point in ('before-tag', 'before-release', 'after-release'):
            with self.subTest(point=point):
                self.interrupt = point
                tag = 'v2.15.' + str(2 + ('before-tag', 'before-release', 'after-release').index(point))
                state = self.new_state(tag)
                with self.assertRaisesRegex(ValueError, 'injected'):
                    self.write(state)
                state['head'] = self.head()
                self.assertEqual(self.write(state), 'main-and-tag')
                self.assertEqual(self.latest, tag)


class PrepareTests(DeliveryTests):
    def setUp(self):
        super().setUp()
        import shutil
        import subprocess
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / 'app-source'
        self.source.mkdir()
        for name in delivery.package.CANONICAL:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / 'plugins/hitkeep-analytics' / name).read_bytes())
        def git(*args):
            return subprocess.check_output(['git', '-C', str(self.source), *args], stderr=subprocess.PIPE).decode().strip()
        git('init', '-q'); git('add', '.')
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'canonical source')
        self.sha = git('rev-parse', 'HEAD')
        self.manifest['source_commit'] = self.sha
        generated = self.base / 'generated'
        generated.mkdir()
        for name in ('plugins', '.claude-plugin', '.agents'):
            shutil.copytree(ROOT / name, generated / name)
        delivery.package.sync(self.source, self.sha, '2.15.1', generated)
        output = self.base / 'package.zip'
        delivery.package.package(output, generated)
        self.raw = output.read_bytes()
        self.update_digest()
        self.release = dict(tag_name='v2.15.1', draft=False, prerelease=False,
                            assets=[dict(id=1, name='release-candidate.json'), dict(id=2, name='hitkeep-analytics-2.15.1.zip')])
        self.app_private = False
        self.fail_signature = False
        self.actual_tag = self.sha
        self.original_command = delivery.command
        self.env = dict(GITHUB_REPOSITORY=delivery.REPO, GITHUB_REF='refs/heads/main',
                        GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_WORKFLOW_REF=delivery.REPO + '/' + delivery.WORKFLOW + '@refs/heads/main')
        self.addCleanup(patch.stopall)
        patch.dict('os.environ', self.env).start()
        patch.object(delivery, 'command', side_effect=self.transport).start()
        patch.object(delivery, 'api', side_effect=self.api).start()
        patch.object(delivery, 'tag_commit', side_effect=lambda repo, tag: self.actual_tag).start()
        patch.object(delivery, 'download', side_effect=lambda entry: json.dumps(self.manifest).encode() if entry['id'] == 1 else self.raw).start()

    def update_digest(self):
        self.digest = hashlib.sha256(self.raw).hexdigest()
        self.manifest['artifacts'][0]['sha256'] = self.digest

    def transport(self, *args, **kwargs):
        args = list(args)
        if args[:3] == ['gh', 'attestation', 'verify']:
            if self.fail_signature:
                raise ValueError('native attestation verification rejected signature')
            self.assertIn('--source-digest', args)
            self.assertEqual(args[args.index('--source-digest') + 1], self.producer)
            self.assertIn('--deny-self-hosted-runners', args)
            return json.dumps(self.attestations).encode()
        if 'fetch' in args and 'https://github.com/' + delivery.APP + '.git' in args:
            args[args.index('https://github.com/' + delivery.APP + '.git')] = str(self.source)
        elif 'fetch' in args:
            return b''
        return self.original_command(*args, **kwargs)

    def api(self, repo, path, optional=False):
        if repo == delivery.APP:
            if path == '': return dict(private=self.app_private)
            if path.startswith('releases/tags/'): return self.release
            if path.startswith('actions/runs/'): return self.run
            if path.startswith('compare/'): return dict(status='ahead')
        if repo == delivery.REPO:
            if path == 'branches/main':
                return dict(protected=False, commit=dict(sha=delivery.package.git(ROOT, 'rev-parse', 'HEAD').decode().strip()))
            if path == 'rulesets?includes_parents=true': return []
        raise AssertionError((repo, path))

    def assert_no_write(self):
        writer = Mock()
        with self.assertRaises((ValueError, zipfile.BadZipFile)):
            delivery.deliver('v2.15.1', self.sha, self.digest, ROOT, writer=writer)
        writer.assert_not_called()

    def rewrite_zip(self, name, data):
        buffer = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(self.raw)) as old, zipfile.ZipFile(buffer, 'w') as new:
            for entry in old.infolist():
                new.writestr(entry, data if entry.filename == name else old.read(entry))
        self.raw = buffer.getvalue()
        self.update_digest()

    def test_complete_verified_candidate_reaches_writer_once(self):
        writer = Mock(return_value='verified-fixture')
        report = delivery.deliver('v2.15.1', self.sha, self.digest, ROOT, writer=writer)
        writer.assert_called_once()
        self.assertEqual(report['disposition'], 'verified-fixture')
        self.assertEqual(set(writer.call_args.args[0]['tree']), delivery.package.GENERATED)

    def test_faults_never_reach_writer(self):
        scenarios = ('private', 'draft', 'prerelease', 'moved-tag', 'wrong-attempt', 'wrong-signer',
                     'digest', 'malformed-zip', 'version', 'source', 'rehashed-content', 'network', 'signature')
        for case in scenarios:
            with self.subTest(case=case):
                original = (self.app_private, copy.deepcopy(self.release), self.actual_tag, copy.deepcopy(self.run),
                            copy.deepcopy(self.certificate), self.raw, self.digest, copy.deepcopy(self.manifest))
                if case == 'signature': self.fail_signature = True
                if case == 'private': self.app_private = True
                if case == 'draft': self.release['draft'] = True
                if case == 'prerelease': self.release['prerelease'] = True
                if case == 'moved-tag': self.actual_tag = 'd' * 40
                if case == 'wrong-attempt': self.run['run_attempt'] = 1
                if case == 'wrong-signer': self.certificate['buildSignerURI'] = 'untrusted'
                if case == 'digest': self.raw += b'tamper'
                if case == 'malformed-zip':
                    self.raw = b'not a zip'; self.update_digest()
                if case == 'version':
                    self.rewrite_zip('hitkeep-analytics/plugin.json', b'{"version":"0.0.0"}')
                if case in ('source', 'rehashed-content'):
                    with zipfile.ZipFile(io.BytesIO(self.raw)) as archive:
                        inventory = json.loads(archive.read('hitkeep-analytics/source.json'))
                    if case == 'source': inventory['commit'] = 'd' * 40
                    else:
                        relative = 'skills/hitkeep-analytics/SKILL.md'
                        self.rewrite_zip('hitkeep-analytics/' + relative, b'tamper')
                        inventory['sha256'][relative] = hashlib.sha256(b'tamper').hexdigest()
                    self.rewrite_zip('hitkeep-analytics/source.json', json.dumps(inventory).encode())
                if case == 'network':
                    with patch.object(delivery, 'api', side_effect=ValueError('network/auth denied')):
                        self.assert_no_write()
                else: self.assert_no_write()
                self.app_private, self.release, self.actual_tag, self.run, saved, self.raw, self.digest, self.manifest = original
                self.certificate.clear(); self.certificate.update(saved)
                self.fail_signature = False


if __name__ == '__main__':
    unittest.main()
