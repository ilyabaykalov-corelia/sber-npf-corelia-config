import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RELEASE = Path(sys.argv.pop(1)).resolve()
PLATFORM = ROOT.parent / 'sber-npf-platform-v'
spec = importlib.util.spec_from_file_location('package_solution', ROOT / 'scripts/package-solution.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SolutionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=RELEASE.parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.release = self.root / 'compiled'
        shutil.copytree(RELEASE, self.release)
        self.platform = self.root / 'platform'
        metadata = module.read_json(PLATFORM / '.info.meta.json')
        for name in ['.info.meta.json'] + [f['path'][1:] for f in metadata['files']]:
            destination = self.platform / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(PLATFORM / name, destination)
        self.output = self.root / 'solution'

    def build(self):
        return module.assemble(self.release, self.platform, self.output)

    def change_json(self, path, change):
        value = module.read_json(path)
        change(value)
        path.write_text(json.dumps(value), encoding='utf-8')

    def test_complete_reproducible_package_preserves_unrelated_permissions(self):
        self.build()
        before = module.indexed(module.read_json(self.platform / 'model.graphql-permissions.json'))
        after = module.indexed(module.read_json(self.output / 'platform-v/model.graphql-permissions.json'))
        fragment = module.indexed(module.read_json(self.release / 'platform-v/graphql-permissions.fragment.json'))
        self.assertEqual(set(before), set(after))
        for name in before:
            self.assertEqual(after[name], fragment.get(name, before[name]))
        with zipfile.ZipFile(self.output / 'platform-v.zip') as archive:
            expected = {'.info.meta.json'} | {f['path'][1:] for f in module.read_json(self.platform / '.info.meta.json')['files']}
            self.assertEqual(set(archive.namelist()), expected)
            for name in expected:
                self.assertEqual(archive.read(name), (self.output / 'platform-v' / name).read_bytes())
        inventory = module.read_json(self.output / 'solution-manifest.json')['sha256']
        for name, checksum in inventory.items():
            self.assertEqual(module.sha((self.output / name).read_bytes()), checksum)
        again = self.root / 'again'
        module.assemble(self.release, self.platform, again)
        self.assertEqual((self.output / 'solution-manifest.json').read_bytes(), (again / 'solution-manifest.json').read_bytes())

    def test_rejects_changed_runtime_operation(self):
        file = next((self.release / 'corelia/graphql').glob('*.graphql'))
        file.write_text(file.read_text() + '\n')
        with self.assertRaisesRegex(ValueError, 'checksum/body'): self.build()
        self.assertFalse(self.output.exists())

    def test_rejects_permission_rule_drift(self):
        name = module.read_json(self.release / 'platform-v/graphql-permissions.fragment.json')[0]['name']
        def change(value):
            next(p for p in value if p['name'] == name)['checkForAnyPrivilege'] = ['unapproved:scope']
        self.change_json(self.platform / 'model.graphql-permissions.json', change)
        with self.assertRaisesRegex(ValueError, 'permission rules differ'): self.build()

    def test_rejects_missing_process(self):
        self.change_json(self.platform / 'dictionary/DocumentProcessSettings.json', lambda v: v['objects'][0].update(processId='missing'))
        with self.assertRaisesRegex(ValueError, 'creation process'): self.build()

    def test_rejects_manifest_path_escape(self):
        self.change_json(self.platform / '.info.meta.json', lambda v: v['files'].append({'path': '/../outside'}))
        with self.assertRaisesRegex(ValueError, 'Unsafe path'): self.build()

    def test_does_not_overwrite_existing_release(self):
        self.output.mkdir()
        (self.output / 'keep').write_text('existing')
        with self.assertRaisesRegex(ValueError, 'Output must be new'): self.build()
        self.assertEqual((self.output / 'keep').read_text(), 'existing')


if __name__ == '__main__':
    unittest.main()
