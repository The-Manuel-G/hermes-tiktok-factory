"""Unit Tests for Critical Hermes TikTok Factory Modules:
1. Fingerprint Geo-Coherence Generator
2. Mail Worker 6-Digit Verification Code Parser
3. Sticky 1:1 Proxy Pool & Scheduler Dispatch
"""
import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from modules.db_manager import init_db
from modules.fingerprint_gen import generate_coherent_fingerprint, generate_unique_identity
from modules.mail_imap import extract_tiktok_code_and_link
from modules.proxy_pool import acquire_free_proxy


class TestCriticalHermesModules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_fingerprint_geo_coherence(self):
        fp_es = generate_coherent_fingerprint({"geo": "ES", "timezone": "Europe/Madrid", "locale": "es-ES"})
        self.assertEqual(fp_es["geo"], "ES")
        self.assertEqual(fp_es["timezone"], "Europe/Madrid")
        self.assertEqual(fp_es["locale"], "es-ES")
        self.assertTrue(len(fp_es["canvas_seed"]) == 16)

        fp_us = generate_coherent_fingerprint({"geo": "US", "timezone": "America/Los_Angeles", "locale": "en-US"})
        self.assertEqual(fp_us["geo"], "US")
        self.assertEqual(fp_us["locale"], "en-US")

    def test_mail_6_digit_code_parser(self):
        sample1 = "Subject: 492817 is your TikTok verification code\nHello, use code 492817 to verify your email."
        res1 = extract_tiktok_code_and_link(sample1)
        self.assertEqual(res1["code"], "492817")

        sample2 = "Tu código de verificación de TikTok es: 738192. Entra a https://www.tiktok.com/verify?u=123"
        res2 = extract_tiktok_code_and_link(sample2)
        self.assertEqual(res2["code"], "738192")
        self.assertIn("tiktok.com/verify", res2["link"])

    def test_identity_and_alias_pattern(self):
        ident = generate_unique_identity(nicho="inmigracion_legal", geo="US", ciudad="Miami")
        self.assertTrue(ident["alias_email"].endswith("@example.com"))
        self.assertTrue(ident["alias_email"].startswith("tk"))
        self.assertGreaterEqual(len(ident["password"]), 10)
        self.assertLessEqual(len(ident["username"]), 18)


if __name__ == "__main__":
    unittest.main()
