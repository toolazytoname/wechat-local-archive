"""Installer state/safety contracts; mock subprocesses are NOT install evidence."""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('wla_bootstrap', Path(__file__).resolve().parents[1]/'scripts/bootstrap.py')
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root/'support'
        self.source = self.root/'synthetic.whl';self.source.write_bytes(b'SYNTHETIC NOT A REAL WHEEL')
        self.args = argparse.Namespace(source=str(self.source),app_support=str(self.home),yes=True,offline=False,wheelhouse=None)
        self.calls=[]

    def runner(self, command, **kwargs):
        self.calls.append(command)
        log=kwargs['log'];log.write_text('synthetic runner only');log.chmod(0o600)
        if 'venv' in command:
            target=Path(command[-1]);(target/'bin').mkdir()
            (target/'bin/python').write_text('synthetic marker; never execute')

    def install(self):
        return bootstrap.install(self.args,runner=self.runner)

    def test_noninteractive_without_yes_has_no_files_or_subprocess(self):
        self.args.yes=False
        with patch.object(bootstrap.sys.stdin,'isatty',return_value=False):
            result=self.install()
        self.assertEqual(result['status'],'cancelled')
        self.assertFalse(self.home.exists())
        self.assertEqual(self.calls,[])

    def test_two_versions_and_rollback_preserve_old_files(self):
        first=self.install();second=self.install()
        self.assertNotEqual(first['install_id'],second['install_id'])
        self.assertEqual(bootstrap.active_id(self.home),second['install_id'])
        self.assertEqual(second['previous_install_id'],first['install_id'])
        result=bootstrap.rollback(self.args)
        self.assertEqual(result['install_id'],first['install_id'])
        self.assertTrue((self.home/'versions'/second['install_id']).is_dir())
        self.assertEqual(len(list((self.home/'versions').iterdir())),2)
        for command in self.calls:
            self.assertIn('-I',command)
        self.assertTrue(any(command[-1]=='check' for command in self.calls))

    def test_failures_never_replace_active_installation(self):
        first=self.install()
        for failure in ('venv.log','pip.log','dependency-check.log','smoke.log'):
            with self.subTest(failure=failure):
                def fail(command,**kwargs):
                    if kwargs['log'].name==failure:
                        raise subprocess.CalledProcessError(1,command)
                    self.runner(command,**kwargs)
                with self.assertRaises(bootstrap.InstallError):
                    bootstrap.install(self.args,runner=fail)
                self.assertEqual(bootstrap.active_id(self.home),first['install_id'])
        receipts=[json.loads(p.read_text()) for p in self.home.glob('versions/*/install-receipt.json')]
        self.assertEqual(sum(r['state']=='failed' for r in receipts),4)

    def test_legacy_environment_is_not_rebuilt_or_deleted(self):
        legacy=self.home/'venv/bin';legacy.mkdir(parents=True)
        python=legacy/'python';python.write_text('existing synthetic environment')
        result=self.install()
        self.assertEqual(result['previous_install_id'],'legacy')
        self.assertEqual(python.read_text(),'existing synthetic environment')
        bootstrap.rollback(self.args)
        self.assertEqual(bootstrap.active_id(self.home),'legacy')
        self.assertTrue((self.home/'versions'/result['install_id']).is_dir())

    def test_invalid_current_pointer_and_occupied_file_fail_closed(self):
        bootstrap.checked_root(self.home,create=True)
        for target in ('../foreign','versions/not-an-id'):
            (self.home/'current').symlink_to(target)
            with self.assertRaises(bootstrap.InstallError):self.install()
            (self.home/'current').unlink()
        (self.home/'current').write_text('owned by another operation')
        with self.assertRaises(bootstrap.InstallError):self.install()
        self.assertEqual((self.home/'current').read_text(),'owned by another operation')

    def test_installation_lock_serializes_updates(self):
        bootstrap.checked_root(self.home,create=True)
        with bootstrap.installation_lock(self.home):
            with self.assertRaises(bootstrap.InstallError):self.install()
        self.assertEqual(self.calls,[])

    def test_wheel_mutation_prevents_activation(self):
        def mutate(command,**kwargs):
            self.runner(command,**kwargs)
            if kwargs['log'].name=='smoke.log':self.source.write_bytes(b'changed synthetic wheel')
        with self.assertRaises(bootstrap.InstallError):bootstrap.install(self.args,runner=mutate)
        self.assertIsNone(bootstrap.active_id(self.home))

    def test_offline_has_no_dependency_or_index_resolution(self):
        wheels=self.root/'wheels';wheels.mkdir();(wheels/'synthetic-dependency.whl').write_bytes(b'synthetic')
        self.args.offline=True;self.args.wheelhouse=str(wheels)
        self.install()
        command=next(c for c in self.calls if 'install' in c)
        self.assertIn('--no-index',command);self.assertIn('--no-deps',command)
        self.assertIn(str((wheels/'synthetic-dependency.whl').resolve()),command)
        self.assertTrue(all('http' not in part for part in command))
        self.args.source=str(wheels)
        with self.assertRaises(bootstrap.InstallError):self.install()

    def test_interrupt_after_atomic_activation_keeps_valid_receipt(self):
        original=bootstrap.activate
        def commit_then_interrupt(*args):
            original(*args)
            raise KeyboardInterrupt()
        with patch.object(bootstrap,'activate',side_effect=commit_then_interrupt):result=self.install()
        self.assertEqual(result['status'],'installed')
        self.assertEqual(bootstrap.active_id(self.home),result['install_id'])

    def test_unsafe_install_root_is_not_chmodded(self):
        self.home.mkdir(mode=0o777);self.home.chmod(0o777)
        with self.assertRaises(bootstrap.InstallError):self.install()
        self.assertEqual(self.home.stat().st_mode&0o777,0o777)
        self.assertEqual(self.calls,[])
