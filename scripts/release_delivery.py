#!/usr/bin/env python3
"""Receive one attested app release using reviewed assistant main code."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile

import package

APP = 'HitKeep/hitkeep'
REPO = 'HitKeep/hitkeep-assistant-plugins'
PRODUCER = '.github/workflows/release.yml'
WORKFLOW = '.github/workflows/sync-hitkeep-release.yml'
CHECKS = ('native', 'archives', 'tracker', 'chart', 'docker', 'compose', 'helm', 'docs',
          'assistant-package', 'source-archive')
SHA = re.compile(r'[0-9a-f]{40}')
DIGEST = re.compile(r'[0-9a-f]{64}')


def version(tag):
    if not re.fullmatch(r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', tag):
        raise ValueError('expected vX.Y.Z stable release tag')
    return tuple(map(int, tag[1:].split('.')))


def command(*args, data=None, optional=False, env=None):
    result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    if result.returncode:
        if optional and b'(HTTP 404)' in result.stderr:
            return None
        raise ValueError('release transport or Git operation failed; inspect native run permissions/state')
    return result.stdout


def api(repo, path, optional=False):
    raw = command('gh', 'api', f'repos/{repo}/{path}', optional=optional)
    return json.loads(raw) if raw is not None else None


def asset(release, name):
    matches = [entry for entry in release['assets'] if entry['name'] == name]
    if len(matches) != 1:
        raise ValueError('missing or duplicate app release asset: ' + name)
    return matches[0]


def download(entry):
    return command('gh', 'api', f"repos/{APP}/releases/assets/{entry['id']}",
                   '-H', 'Accept: application/octet-stream')


def tag_commit(repo, tag):
    ref = api(repo, 'git/ref/tags/' + tag, optional=True)
    if ref is None:
        return None
    obj = ref['object']
    if obj['type'] == 'tag':
        obj = api(repo, 'git/tags/' + obj['sha'])['object']
    if obj['type'] != 'commit' or not SHA.fullmatch(obj['sha']):
        raise ValueError('release tag does not identify an immutable commit')
    return obj['sha']


def validate_dispatch(env):
    expected = dict(GITHUB_REPOSITORY=REPO, GITHUB_REF='refs/heads/main',
                    GITHUB_EVENT_NAME='workflow_dispatch',
                    GITHUB_WORKFLOW_REF=REPO + '/' + WORKFLOW + '@refs/heads/main')
    if any(env.get(key) != value for key, value in expected.items()):
        raise ValueError('unauthorized receiver actor or workflow ref')


def validate_policy(branch, rules):
    # This receiver is approved only for the observed unprotected, ruleset-free repository.
    if branch.get('protected') is not False or rules != []:
        raise ValueError('repository protection policy changed; maintainer action required')


def validate_release(repository, release, actual_sha, tag, sha):
    if (repository.get('private') is not False or release.get('tag_name') != tag or
            release.get('draft') is not False or release.get('prerelease') is not False or actual_sha != sha):
        raise ValueError('app release must be public, stable and bound to the unchanged source tag')


def validate_candidate(m, run, attestations, tag, sha, digest):
    version(tag)
    if not SHA.fullmatch(sha) or not DIGEST.fullmatch(digest):
        raise ValueError('invalid source identity or package digest')
    if (m.get('schema_version') != 1 or m.get('tag') != tag or m.get('source_commit') != sha or
            not SHA.fullmatch(m.get('producer_workflow_sha', '')) or
            any(type(m.get(key)) is not int or m[key] < 1 for key in ('producer_run_id', 'producer_run_attempt')) or
            m.get('checks') != dict.fromkeys(CHECKS, 'success')):
        raise ValueError('invalid or incomplete candidate identity/checks')
    items = m['artifacts']
    names = [item['name'] for item in items]
    if len(names) != len(set(names)):
        raise ValueError('duplicate candidate asset')
    matches = [item for item in items if item['name'] == 'hitkeep-analytics-' + tag[1:] + '.zip']
    if matches != [dict(name='hitkeep-analytics-' + tag[1:] + '.zip', type='assistant-package', sha256=digest)]:
        raise ValueError('candidate package identity or digest mismatch')
    if (run.get('id') != m['producer_run_id'] or run.get('run_attempt') != m['producer_run_attempt'] or
            run.get('head_sha') != m['producer_workflow_sha'] or run.get('path') != PRODUCER or
            run.get('head_branch') != 'main' or run.get('event') not in ('push', 'workflow_dispatch') or
            run.get('status') != 'completed' or run.get('conclusion') != 'success' or
            run.get('repository', {}).get('full_name') != APP or run.get('head_repository', {}).get('full_name') != APP):
        raise ValueError('untrusted or unsuccessful producer attempt')
    expected = dict(sourceRepositoryURI='https://github.com/' + APP,
                    sourceRepositoryDigest=m['producer_workflow_sha'], sourceRepositoryRef='refs/heads/main',
                    buildSignerURI='https://github.com/' + APP + '/' + PRODUCER + '@refs/heads/main',
                    runInvocationURI=f"https://github.com/{APP}/actions/runs/{m['producer_run_id']}/attempts/{m['producer_run_attempt']}")
    certificates = [item['verificationResult']['signature']['certificate'] for item in attestations]
    if not any(all(cert.get(key) == value for key, value in expected.items()) for cert in certificates):
        raise ValueError('attestation signer, source or producer attempt mismatch')


def unpack(raw, root):
    expected = {f'{package.NAME}/{path}' for path in package.CANONICAL | {
        'plugin.json', '.claude-plugin/plugin.json', 'source.json', 'README.md'}}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries) != len(expected) or {item.filename for item in entries} != expected:
            raise ValueError('unsafe or incomplete package ZIP allowlist')
        if any((item.external_attr >> 16) != 0o100644 or item.file_size > 1024 * 1024 for item in entries):
            raise ValueError('non-regular, executable or oversized package ZIP content')
        files = {item.filename: archive.read(item) for item in entries}
    # Every name and member is checked in memory before even creating the output directory.
    root.mkdir()
    for relative, data in files.items():
        path = root / 'plugins' / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def policy():
    branch = api(REPO, 'branches/main')
    validate_policy(branch, api(REPO, 'rulesets?includes_parents=true'))
    return branch['commit']['sha']


def prepare(tag, sha, digest, root, temp):
    version(tag)
    if not SHA.fullmatch(sha) or not DIGEST.fullmatch(digest):
        raise ValueError('invalid source identity or package digest')
    # workflow_dispatch authorization belongs to GitHub; inputs cannot select code or write paths.
    validate_dispatch(os.environ)
    repository = api(APP, '')
    release = api(APP, 'releases/tags/' + tag)
    validate_release(repository, release, tag_commit(APP, tag), tag, sha)
    head = policy()
    command('git', '-C', str(root), 'fetch', '-q', 'origin', 'refs/heads/main')
    mraw = download(asset(release, 'release-candidate.json'))
    manifest = json.loads(mraw)
    producer_sha = manifest.get('producer_workflow_sha', '')
    if not SHA.fullmatch(producer_sha):
        raise ValueError('invalid producer workflow SHA')
    mpath = temp / 'release-candidate.json'
    mpath.write_bytes(mraw)
    attestations = json.loads(command('gh', 'attestation', 'verify', str(mpath), '--repo', APP,
        '--signer-workflow', APP + '/' + PRODUCER, '--source-digest', producer_sha,
        '--source-ref', 'refs/heads/main', '--deny-self-hosted-runners', '--format', 'json'))
    for key in ('producer_run_id', 'producer_run_attempt'):
        if type(manifest.get(key)) is not int or manifest[key] < 1:
            raise ValueError('invalid producer run identity')
    run = api(APP, f"actions/runs/{manifest['producer_run_id']}/attempts/{manifest['producer_run_attempt']}")
    validate_candidate(manifest, run, attestations, tag, sha, digest)
    for commit in {sha, producer_sha}:
        if api(APP, f'compare/{commit}...main')['status'] not in ('ahead', 'identical'):
            raise ValueError('app source or producer is outside main history')
    raw = download(asset(release, 'hitkeep-analytics-' + tag[1:] + '.zip'))
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('downloaded app package digest mismatch')
    stage = temp / 'generated'
    unpack(raw, stage)
    for name in ('.claude-plugin', '.agents'):
        shutil.copytree(root / name, stage / name)
    plugin = stage / 'plugins' / package.NAME
    (stage / 'LICENSE').write_bytes((plugin / 'LICENSE').read_bytes())
    catalog = stage / '.claude-plugin/marketplace.json'
    data = json.loads(catalog.read_text())
    data['plugins'][0]['version'] = tag[1:]
    package.write_json(catalog, data)
    for name in ('plugin.json', '.claude-plugin/plugin.json'):
        expected = json.loads((root / 'plugins' / package.NAME / name).read_text())
        expected['version'] = tag[1:]
        if json.loads((plugin / name).read_text()) != expected:
            raise ValueError('package native manifest changed beyond version')
    if (plugin / 'README.md').read_bytes() != (root / 'plugins' / package.NAME / 'README.md').read_bytes():
        raise ValueError('package README differs from reviewed template')
    inventory = json.loads((plugin / 'source.json').read_text())
    if set(inventory) != {'repository', 'commit', 'packager_commit', 'sha256'} or not SHA.fullmatch(inventory.get('packager_commit', '')):
        raise ValueError('missing or unexpected package provenance')
    command('git', '-C', str(root), 'merge-base', '--is-ancestor', inventory['packager_commit'], head)
    app = temp / 'app'
    command('git', 'init', '-q', str(app))
    command('git', '-C', str(app), 'fetch', '-q', '--depth=1', 'https://github.com/' + APP + '.git', sha)
    package.verify_source(app, sha, stage)
    if package.verify(stage)[0] != tag[1:]:
        raise ValueError('package version differs from release')
    return dict(tag=tag, sha=sha, digest=digest, head=head, release=release,
                candidate=mraw, tree={path: (stage / path).read_bytes() for path in package.GENERATED})


def matching_tree(root, commit, files):
    roots = ('plugins/' + package.NAME, '.claude-plugin', '.agents/plugins', 'LICENSE')
    entries = {}
    for entry in package.git(root, 'ls-tree', '-r', '-z', commit, '--', *roots).split(b'\0'):
        if entry:
            metadata, path = entry.split(b'\t', 1)
            entries[path.decode()] = tuple(metadata.decode().split()[:2])
    if (set(files) != package.GENERATED or
            entries != dict.fromkeys(package.GENERATED, ('100644', 'blob'))):
        raise ValueError('generated tree membership or regular non-executable modes differ from allowlist')
    actual = {path: package.git(root, 'show', commit + ':' + path) for path in package.GENERATED}
    with tempfile.TemporaryDirectory(prefix='hitkeep-assistant-tree-') as directory:
        stage = Path(directory)
        for relative, data in actual.items():
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        package.verify(stage)
    for path in package.GENERATED:
        if path.endswith('/marketplace.json'):
            current, expected = json.loads(actual[path]), json.loads(files[path])
            if path == '.claude-plugin/marketplace.json':
                # Main may hold another version; all other catalog content stays reviewed.
                current['plugins'][0]['version'] = expected['plugins'][0]['version']
            if current != expected:
                raise ValueError('generated native catalog content differs from reviewed template')
    return actual == files


def guard(state):
    if policy() != state['head']:
        raise ValueError('assistant main changed; rerun the native workflow to revalidate')
    if api(REPO, 'releases/latest')['tag_name'] != state['latest_tag']:
        raise ValueError('assistant latest changed; rerun the native workflow to revalidate')
    release = api(APP, 'releases/tags/' + state['tag'])
    validate_release(api(APP, ''), release, tag_commit(APP, state['tag']), state['tag'], state['sha'])
    if (download(asset(release, 'release-candidate.json')) != state['candidate'] or
            hashlib.sha256(download(asset(release, 'hitkeep-analytics-' + state['tag'][1:] + '.zip'))).hexdigest() != state['digest']):
        raise ValueError('app release assets changed after validation')


def release_notes(state):
    return ('Canonical skills for HitKeep ' + state['tag'] + '.\n\n' +
            '[Download the verified app ZIP](https://github.com/' + APP + '/releases/download/' + state['tag'] +
            '/hitkeep-analytics-' + state['tag'][1:] + '.zip)\n\n' +
            'App source: `' + state['sha'] + '`\nPackage SHA-256: `' + state['digest'] + '`\n')


def validate_assistant_release(release, state):
    if release is not None and (release['draft'] or release['prerelease'] or release['tag_name'] != state['tag'] or
            release['body'].replace('\r\n', '\n') != release_notes(state) or release['assets']):
        raise ValueError('immutable assistant release link/content conflict')


def write(state, root, temp):
    gitroot = temp / 'receiver'
    command('git', 'clone', '-q', '--no-hardlinks', '--no-checkout', str(root), str(gitroot))
    command('git', '-C', str(gitroot), 'remote', 'set-url', 'origin', 'https://github.com/' + REPO + '.git')
    command('git', '-C', str(gitroot), 'fetch', '-q', 'origin', 'refs/heads/main')
    head = state['head']
    current = json.loads(package.git(gitroot, 'show', head + ':plugins/' + package.NAME + '/plugin.json'))['version']
    latest = api(REPO, 'releases/latest')
    state['latest_tag'] = latest['tag_name']
    newest = version(state['tag']) >= max(version('v' + current), version(latest['tag_name']))
    release = api(REPO, 'releases/tags/' + state['tag'], optional=True)
    validate_assistant_release(release, state)
    existing = tag_commit(REPO, state['tag'])
    if existing:
        command('git', '-C', str(gitroot), 'fetch', '-q', 'origin', 'refs/tags/' + state['tag'])
        if not matching_tree(gitroot, existing, state['tree']):
            raise ValueError('immutable assistant tag content/provenance conflict')
    matches_main = matching_tree(gitroot, head, state['tree'])
    if version(state['tag']) == version('v' + current) and not matches_main:
        raise ValueError('same assistant main version has conflicting content/provenance')
    commit = head
    if not matches_main:
        command('git', '-C', str(gitroot), 'read-tree', head)
        for path, data in sorted(state['tree'].items()):
            blob = command('git', '-C', str(gitroot), 'hash-object', '-w', '--stdin', data=data).decode().strip()
            command('git', '-C', str(gitroot), 'update-index', '--add', '--cacheinfo', '100644,' + blob + ',' + path)
        tree = command('git', '-C', str(gitroot), 'write-tree').decode().strip()
        env = dict(os.environ, GIT_AUTHOR_NAME='HitKeep Release', GIT_COMMITTER_NAME='HitKeep Release',
                   GIT_AUTHOR_EMAIL='41898282+github-actions[bot]@users.noreply.github.com',
                   GIT_COMMITTER_EMAIL='41898282+github-actions[bot]@users.noreply.github.com')
        commit = command('git', '-C', str(gitroot), 'commit-tree', tree, '-p', head,
                         data=('chore: sync HitKeep ' + state['tag'] + '\n').encode(), env=env).decode().strip()
        paths = command('git', '-C', str(gitroot), 'diff-tree', '--no-commit-id', '--name-only', '-r', commit).decode().splitlines()
        if not paths or not set(paths) <= package.GENERATED:
            raise ValueError('generated diff changes non-allowlisted files')
    push = ('git', '-C', str(gitroot), '-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential', 'push', 'origin')
    if newest and not matches_main:
        guard(state)
        command(*push, commit + ':refs/heads/main')
        state['head'] = commit
        if policy() != commit:
            raise ValueError('assistant main readback mismatch')
    if existing is None:
        guard(state)
        command(*push, commit + ':refs/tags/' + state['tag'])
        existing = commit
    if tag_commit(REPO, state['tag']) != existing:
        raise ValueError('immutable assistant tag readback mismatch')
    state['output_sha'] = existing
    if release is None:
        guard(state)
        body = temp / 'release-notes.md'
        body.write_text(release_notes(state))
        command('gh', 'release', 'create', state['tag'], '--repo', REPO, '--verify-tag', '--title', state['tag'],
                '--notes-file', str(body), '--latest=' + str(newest).lower())
        release = api(REPO, 'releases/tags/' + state['tag'])
        if newest:
            state['latest_tag'] = state['tag']
    validate_assistant_release(release, state)
    if newest and api(REPO, 'releases/latest')['tag_name'] != state['tag']:
        raise ValueError('assistant latest release readback mismatch; inspect native release state')
    guard(state)
    return 'main-and-tag' if newest else 'historical-tag-only'


def deliver(tag, sha, digest, root=package.ROOT, writer=None):
    with tempfile.TemporaryDirectory(prefix='hitkeep-assistant-receive-') as directory:
        temp = Path(directory)
        state = prepare(tag, sha, digest, root, temp)
        disposition = (writer or write)(state, root, temp)
        return dict(tag=tag, source_sha=sha, package_sha256=digest, output_ref='refs/tags/' + tag,
                    output_sha=state.get('output_sha'), main_sha=state['head'], disposition=disposition)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('verify', 'publish'))
    parser.add_argument('--release-tag', required=True)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--package-sha256', required=True)
    args = parser.parse_args()
    if args.action == 'verify':
        with tempfile.TemporaryDirectory(prefix='hitkeep-assistant-verify-') as directory:
            prepare(args.release_tag, args.source_sha, args.package_sha256, package.ROOT, Path(directory))
        report = dict(tag=args.release_tag, source_sha=args.source_sha, package_sha256=args.package_sha256, disposition='verified')
    else:
        report = deliver(args.release_tag, args.source_sha, args.package_sha256)
    print(json.dumps(report, sort_keys=True))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
            summary.write('```json\n' + json.dumps(report, indent=2, sort_keys=True) + '\n```\n')


if __name__ == '__main__':
    main()
