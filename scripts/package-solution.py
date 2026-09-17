#!/usr/bin/env python3
"""Assemble the Sber solution offline from a compiled release and platform sources."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile


def parse_json(source):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate JSON key: {key}')
            result[key] = value
        return result
    return json.loads(source, object_pairs_hook=unique)


def read_json(path):
    return parse_json(path.read_bytes())


def safe_file(root, relative):
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError(f'Unsafe path: {relative}')
    resolved = (root / path).resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError(f'File outside package: {relative}')
    return resolved


def sha(data):
    return hashlib.sha256(data).hexdigest()


def indexed(entries):
    result = {}
    for entry in entries:
        name = entry['name']
        if name in result:
            raise ValueError(f'Duplicate operation: {name}')
        result[name] = entry
    return result


def assemble(release, platform, output):
    release, platform = Path(release).resolve(strict=True), Path(platform).resolve(strict=True)
    output = Path(output).absolute()
    parent = output.parent.resolve(strict=True)
    output = parent / output.name
    if output.exists() or output.is_symlink() or any(output.is_relative_to(p) for p in (release, platform)):
        raise ValueError('Output must be new and outside input packages')
    # Snapshot validated input bytes; subsequent source edits cannot change the assembled release.
    files = {}
    manifest_bytes = safe_file(release, 'manifest.json').read_bytes()
    manifest = parse_json(manifest_bytes)
    config_bytes = safe_file(release, 'corelia/configuration.json').read_bytes()
    config = parse_json(config_bytes)
    if sha(config_bytes) != manifest['configurationSha256']:
        raise ValueError('Configuration checksum mismatch')
    files['corelia/configuration.json'] = config_bytes
    ac = safe_file(release, 'corelia/platform-v-ac.json').read_bytes()
    if sha(ac) != manifest['accessControlSha256'] or ac != safe_file(platform, 'ac.json').read_bytes():
        raise ValueError('Access control differs from compiled release')
    files['corelia/platform-v-ac.json'] = ac
    fragment = indexed(read_json(safe_file(release, 'platform-v/graphql-permissions.fragment.json')))
    if set(fragment) != set(config['operations']) or set(fragment) != set(manifest['operationSha256']):
        raise ValueError('Operation set differs from compiled release')
    for name, operation in config['operations'].items():
        relative = 'corelia/' + operation['file']
        content = safe_file(release, relative).read_bytes()
        if sha(content) != manifest['operationSha256'][name] or content.decode('utf-8') != fragment[name]['body']:
            raise ValueError(f'Operation checksum/body mismatch: {name}')
        files[relative] = content
    metadata_bytes = safe_file(platform, '.info.meta.json').read_bytes()
    metadata = parse_json(metadata_bytes)
    platform_files = {'.info.meta.json': metadata_bytes}
    for entry in metadata['files']:
        raw = entry['path']
        if not raw.startswith('/') or raw.startswith('//'):
            raise ValueError('Invalid platform manifest path')
        relative = raw[1:]
        if relative in platform_files:
            raise ValueError(f'Duplicate manifest file: {relative}')
        platform_files[relative] = safe_file(platform, relative).read_bytes()
    if platform_files['ac.json'] != ac:
        raise ValueError('Platform access control changed while packaging')
    permissions = indexed(parse_json(platform_files['model.graphql-permissions.json']))
    for name, permission in fragment.items():
        original = permissions.get(name)
        if original is None or {k: v for k, v in original.items() if k != 'body'} != {k: v for k, v in permission.items() if k != 'body'}:
            raise ValueError(f'Platform permission rules differ: {name}')
        permissions[name] = permission
    platform_files['model.graphql-permissions.json'] = (json.dumps(list(permissions.values()), ensure_ascii=False, indent=2) + '\n').encode()
    types = {t['id']: t for t in config['documentTypes']}
    if set(types) != {'PDS_CONTRACT', 'KID_OPS'} or len(config['documentTypes']) != 2:
        raise ValueError('This solution supports exactly the two current Sber document types')
    dictionary = parse_json(platform_files['dictionary/DocumentType.json'])['objects']
    if len(dictionary) != 2 or {t['id'] for t in dictionary} != set(types):
        raise ValueError('Document type dictionary differs from runtime')
    processes = set()
    for name, content in platform_files.items():
        if name.endswith(('.xml', '.bpmn')):
            tree = ET.fromstring(content)
            processes.update(n.attrib['id'] for n in tree.iter() if n.tag == '{http://www.omg.org/spec/BPMN/20100524/MODEL}process')
    settings = parse_json(platform_files['dictionary/DocumentProcessSettings.json'])['objects']
    for code, definition in types.items():
        enabled = [s for s in settings if s['documentType'] == code and s.get('enabled') is True]
        workflow = definition['workflow']
        if workflow.get('creationSource', 'platform-settings') != 'platform-settings':
            raise ValueError('Sber solution currently uses the platform process dictionary')
        if len(enabled) != 1 or enabled[0]['processId'] not in processes:
            raise ValueError(f'Inconsistent creation process: {code}')
    files.update({'platform-v/' + name: data for name, data in platform_files.items()})
    files['compiler-manifest.json'] = manifest_bytes
    with tempfile.TemporaryDirectory(prefix='.sber-solution-', dir=parent) as temporary:
        stage = Path(temporary) / 'release'
        stage.mkdir()
        for name, content in files.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        with zipfile.ZipFile(stage / 'platform-v.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in sorted(platform_files.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, content)
        files['platform-v.zip'] = (stage / 'platform-v.zip').read_bytes()
        inventory = {'schemaVersion': 1, 'customer': 'sber-npf', 'coreliaVersion': manifest['coreliaVersion'], 'sha256': {name: sha(data) for name, data in sorted(files.items())}}
        (stage / 'solution-manifest.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        if output.exists():
            raise ValueError('Output appeared while packaging')
        os.rename(stage, output)
    return output


if __name__ == '__main__':
    if len(sys.argv) != 4:
        sys.exit('Usage: package-solution.py COMPILED_RELEASE PLATFORM_SOURCE NEW_OUTPUT')
    try:
        print(assemble(*sys.argv[1:]))
    except (ValueError, KeyError, OSError, ET.ParseError) as error:
        sys.exit(str(error))
