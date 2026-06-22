import unittest

from app.api import grok


class GrokLoggingTests(unittest.TestCase):
    def test_upstream_log_body_redacts_known_keys_and_truncates(self) -> None:
        original_gpt_key = grok.settings.gpt_image2_api_key
        original_grok_key = grok.settings.grok_api_key
        try:
            grok.settings.gpt_image2_api_key = "secret-gpt-key"
            grok.settings.grok_api_key = "secret-grok-key"

            body = "prefix secret-gpt-key middle secret-grok-key " + ("x" * 1200)
            value = grok._upstream_log_body(body, max_chars=120)
        finally:
            grok.settings.gpt_image2_api_key = original_gpt_key
            grok.settings.grok_api_key = original_grok_key

        self.assertNotIn("secret-gpt-key", value)
        self.assertNotIn("secret-grok-key", value)
        self.assertIn("[hidden]", value)
        self.assertTrue(value.endswith("..."))
        self.assertLessEqual(len(value), 123)


if __name__ == "__main__":
    unittest.main()
