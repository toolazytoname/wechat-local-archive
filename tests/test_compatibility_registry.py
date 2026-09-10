"""Simulated attestations exercise registry policy, not real-machine evidence."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tests.test_build_fingerprint import fake_bundle, fake_environment
from wechat_export.compatibility_registry import environment_binding, reviewed_support, REGISTRY_SCHEMA, STAGE_KEYS, read_registry
from wechat_export.compatibility import evaluate_environment


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        self.env=fake_environment(fake_bundle(self.root/'Synthetic.app'))
        binding=environment_binding(self.env)
        # A hypothetical reviewed shape used only in this test. Never shipped.
        evidence=[{'evidence_id':f'simulated-{n}','environment_id':f'simulated-host-{n}',
                   'binding':binding,'report_sha256':str(n)*64,'synthetic':False,
                   'fresh_user_first_read':True,'human_sample_accepted':True,
                   'stages':{k:True for k in STAGE_KEYS}} for n in (1,2)]
        self.registry={'schema':REGISTRY_SCHEMA,'entries':[{'binding':binding,'release_reviewed':True,'evidence':evidence}]}

    def test_shipped_registry_is_empty_and_version_list_never_enables_support(self):
        self.assertEqual(read_registry()['entries'],[])
        with patch('wechat_export.adapters.macos_xwechat.VERIFIED_BUILDS',('269630',)):
            self.assertFalse(evaluate_environment(self.env)['supported_for_guided_read'])

    def test_exact_binding_and_all_reviewed_stages_required(self):
        self.assertTrue(reviewed_support(self.env,registry=self.registry)['verified'])
        with patch('wechat_export.compatibility_registry.read_registry',return_value=self.registry):
            report=evaluate_environment(self.env)
        self.assertTrue(report['supported_for_guided_read'])
        self.assertTrue(all(report['stages'][k] for k in STAGE_KEYS))
        for field,value in [('mac_ver','changed-os'),('machine','x86_64'),('wechat_build','269579')]:
            env=copy.deepcopy(self.env);env[field]=value
            self.assertFalse(reviewed_support(env,registry=self.registry)['verified'])
        env=copy.deepcopy(self.env);env['wechat_fingerprint']['sha256']='b'*64
        self.assertFalse(reviewed_support(env,registry=self.registry)['verified'])
        env=copy.deepcopy(self.env);env['wechat_codesign']['verified']=False
        self.assertFalse(reviewed_support(env,registry=self.registry)['verified'])

    def test_duplicate_synthetic_partial_or_unreviewed_evidence_rejected(self):
        mutations=[lambda e:e.update(release_reviewed=False),
                   lambda e:e['evidence'].pop(),
                   lambda e:e['evidence'][1].update(environment_id='simulated-host-1'),
                   lambda e:e['evidence'][1].update(report_sha256='1'*64),
                   lambda e:e['evidence'][1].update(synthetic=True),
                   lambda e:e['evidence'][1].update(human_sample_accepted='true'),
                   lambda e:e['evidence'][1]['stages'].update(codec_verified=False)]
        for index,mutate in enumerate(mutations):
            with self.subTest(index=index):
                registry=copy.deepcopy(self.registry);mutate(registry['entries'][0])
                self.assertFalse(reviewed_support(self.env,registry=registry)['verified'])

    def test_local_legacy_or_matching_report_never_promotes_published_support(self):
        private=self.root/'private';private.mkdir()
        report=private/'compatibility-evidence.json'
        report.write_text(json.dumps({'wechat_build':'269630','stages':{k:True for k in STAGE_KEYS}}))
        self.assertIsNone(evaluate_environment(self.env,private_root=private)['this_machine_evidence'])
        report.write_text(json.dumps({'binding':environment_binding(self.env),'stages':{k:True for k in STAGE_KEYS}}))
        value=evaluate_environment(self.env,private_root=private)
        self.assertTrue(value['this_machine_evidence']['binding_matches'])
        self.assertFalse(value['supported_for_guided_read'])
        self.assertIsNone(evaluate_environment(self.env)['this_machine_evidence'])
