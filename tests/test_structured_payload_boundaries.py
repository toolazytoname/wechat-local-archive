import unittest
from wechat_export.preview import analysis_record, classify_payload


class StructuredPayloadBoundaryTests(unittest.TestCase):
    def test_self_closing_unknown_and_system_roots_are_not_plaintext(self):
        for kind, text in [
            ('image','<img aeskey="SYNTHETIC_CREDENTIAL"/>'),
            ('video','<videomsg cdnurl="SYNTHETIC_CREDENTIAL"/>'),
            ('system','<sysmsg credential="SYNTHETIC_CREDENTIAL"/>'),
            ('unknown_999','<unknown_payload credential="SYNTHETIC_CREDENTIAL"/>'),
            ('text','<msg/>'),
            ('text','\ufeff<unknown_payload credential="SYNTHETIC_CREDENTIAL"/>'),
        ]:
            with self.subTest(kind=kind,text=text):
                info=classify_payload(text,kind)
                self.assertFalse(info['readable'])
                projected=analysis_record({'message_type_normalized':kind,'text':text})
                self.assertIsNone(projected['text'])
                self.assertNotIn('SYNTHETIC_CREDENTIAL',projected['preview'])

    def test_structured_title_is_not_a_second_raw_payload_channel(self):
        raw='<msg><img/><title><![CDATA[<payload aeskey="SYNTHETIC_CREDENTIAL"/>]]></title></msg>'
        projected=analysis_record({'message_type_normalized':'image','text':raw})
        self.assertNotIn('SYNTHETIC_CREDENTIAL',projected['preview'])
        self.assertIsNone(projected['media_title'])

    def test_literal_text_and_ordinary_name_prefix_are_preserved(self):
        for text in ('Alice:\nplease keep this line','2 < 3 and 5 > 4','Literal markup: <img src="example"> stays literal'):
            with self.subTest(text=text):
                self.assertEqual(analysis_record({'message_type_normalized':'text','text':text})['text'],text)
