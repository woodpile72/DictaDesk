# SPDX-License-Identifier: GPL-3.0-only
"""Model download compression must not select incompatible system Brotli."""

import gzip
import unittest
from unittest.mock import patch

import httpx2

from dictadesk.audio.worker import configure_hub_downloads


class DownloadCompressionTests(unittest.TestCase):
    def test_negotiates_builtin_decoder_and_reads_compressed_model_config(self):
        payload = b'{"model_type":"whisper"}'

        def respond(request):
            offered = {part.strip() for part in request.headers["Accept-Encoding"].split(",")}
            self.assertNotIn("br", offered)
            self.assertIn("gzip", offered)
            return httpx2.Response(
                200, headers={"Content-Encoding": "gzip"}, content=gzip.compress(payload)
            )

        with httpx2.Client(transport=httpx2.MockTransport(respond)) as client:
            # Simulate HTTPX detecting an older system Brotli package.
            client.headers["Accept-Encoding"] = "gzip, deflate, br"
            with patch("huggingface_hub.utils.get_session", return_value=client):
                configure_hub_downloads()
            self.assertEqual(client.get("https://example.invalid/config.json").content, payload)
