"""Check the distribution entry point without starting any browser."""
import json
from pathlib import Path
import unittest

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent


class LauncherTests(unittest.TestCase):
    def test_root_only_has_launcher_and_app(self):
        self.assertEqual(sorted(p.name for p in ROOT.iterdir() if p.name != '.git'), ['app', 'start.cmd'])
        self.assertEqual(sorted(p.name for p in ROOT.glob('*.cmd')), ['start.cmd'])

    def test_cmd_has_crlf_without_bom(self):
        data = (ROOT / 'start.cmd').read_bytes()
        self.assertTrue(data.startswith(b'@echo off\r\n'))
        self.assertTrue(data.endswith(b'\r\n'))
        self.assertNotIn(b'\n', data.replace(b'\r\n', b''))
        self.assertNotIn(b'\r', data.replace(b'\r\n', b''))
        data.decode('ascii')

    def test_launcher_is_relative_and_policy_is_process_scoped(self):
        text = (ROOT / 'start.cmd').read_text()
        self.assertIn('"%~dp0app\\launch.ps1" %*', text)
        self.assertIn('-NoProfile -ExecutionPolicy Bypass', text)
        self.assertNotIn('Set-ExecutionPolicy', text)
        self.assertNotIn('--live', text)
        self.assertNotIn('C:\\Users', text)

    def test_private_state_is_ignored(self):
        ignored = (APP / '.gitignore').read_text().splitlines()
        for name in ('data/', '.venv/', '.runtime/', '__pycache__/'):
            self.assertIn(name, ignored)
        self.assertIn('*.ps1 text eol=crlf', (APP / '.gitattributes').read_text().splitlines())

    def test_downloads_are_versioned_and_hash_pinned(self):
        downloads = json.loads((APP / 'downloads.json').read_text())
        self.assertIn('python-3.14.6-embed-amd64.zip', downloads['python']['url'])
        self.assertIn('/zipapp/pip-26.1.2.pyz', downloads['pip']['url'])
        for spec in downloads.values():
            self.assertTrue(spec['url'].startswith('https://'))
            self.assertRegex(spec['sha256'], r'^[A-F0-9]{64}$')

    def test_setup_check_does_not_launch_bot(self):
        script = (APP / 'launch.ps1').read_text(encoding='utf-8-sig')
        check_branch = script.split('if ($setupOnly) {', 1)[1].split('} else {', 1)[0]
        self.assertNotIn('& $python', check_branch)
        self.assertNotIn('web_app.py', check_branch)
        self.assertNotIn('Set-ExecutionPolicy', script)

    def test_portable_setup_uses_own_windows_powershell_modules(self):
        script = (APP / 'launch.ps1').read_text(encoding='utf-8-sig')
        for module in ('Utility', 'Archive'):
            self.assertIn(
                f"Import-Module (Join-Path $PSHOME 'Modules\\Microsoft.PowerShell.{module}\\Microsoft.PowerShell.{module}.psd1')",
                script,
            )
        self.assertNotIn('$env:PSModulePath =', script)


if __name__ == '__main__':
    unittest.main()
