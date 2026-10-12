"""Trusted reproduction: hidden/disabled content is not a usable write form."""
import unittest
from form_schema import measure_html


class VisibleWriteFieldsTests(unittest.TestCase):
    def test_hidden_content_is_not_writable(self):
        html = '<form action="/write"><input name="wr_subject" required><textarea name="wr_content" hidden required></textarea></form>'
        self.assertEqual(measure_html(html, 'https://example.org/write', 'write')['state'], 'form_unavailable')

    def test_disabled_content_is_not_writable(self):
        html = '<form action="/write"><input name="wr_subject" required><textarea name="wr_content" disabled required></textarea></form>'
        self.assertEqual(measure_html(html, 'https://example.org/write', 'write')['state'], 'form_unavailable')

    def test_visible_enabled_write_fields_work(self):
        html = '<form action="/write"><input name="wr_subject" required><textarea name="wr_content" required></textarea></form>'
        self.assertEqual(measure_html(html, 'https://example.org/write', 'write')['state'], 'measured')
