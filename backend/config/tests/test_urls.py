from django.test import SimpleTestCase

from config.urls import optional_include


class OptionalIncludeTests(SimpleTestCase):
    def test_skips_missing_module(self):
        patterns = optional_include('missing/', 'apps.agent.__missing_urls__')

        self.assertEqual(patterns, [])

    def test_mounts_existing_module(self):
        patterns = optional_include('auth/', 'django.contrib.auth.urls')

        self.assertEqual(len(patterns), 1)
        self.assertEqual(str(patterns[0].pattern), 'auth/')
