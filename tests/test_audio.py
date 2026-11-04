# SPDX-License-Identifier: GPL-3.0-only
from array import array
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave
from dictadesk.audio.input import Capture, RATE, resolve, check
from dictadesk.audio.preview import PausePreview
from dictadesk.core.settings import Preferences


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.capture = Capture(Preferences(), self.folder)

    def stream(self, block, failure=""):
        capture = self.capture

        class Stream:
            def __init__(self, **kwargs):
                self.callback = kwargs["callback"]

            def __enter__(self):
                for _ in range(4):
                    self.callback(block, len(block) // 2, None, failure)
                capture.stop.set()
                return self

            def __exit__(self, *args):
                pass

        return Stream

    def test_capture_writes_real_wav_and_releases_stream(self):
        block = array("h", [4000] * 1600).tobytes()
        with patch("sounddevice.RawInputStream", self.stream(block)):
            path = self.capture.run(lambda: None)
        with wave.open(str(path)) as audio:
            self.assertEqual(audio.getframerate(), RATE)
            self.assertEqual(audio.getnframes(), 6400)
        self.assertTrue(self.capture.done.is_set())
        self.assertGreater(self.capture.peak, 0.1)

    def test_silence_is_not_sent_for_transcription(self):
        with patch("sounddevice.RawInputStream", self.stream(bytes(3200))):
            with self.assertRaisesRegex(RuntimeError, "No speech"):
                self.capture.run(lambda: None)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_audio_overflow_fails_and_removes_file(self):
        with patch("sounddevice.RawInputStream", self.stream(bytes(3200), "input overflow")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self.capture.run(lambda: None)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_cancel_before_start_never_opens_microphone(self):
        self.capture.cancel.set()
        with patch("sounddevice.RawInputStream") as stream:
            with self.assertRaises(InterruptedError):
                self.capture.run(lambda: None)
            stream.assert_not_called()
        self.assertTrue(self.capture.done.is_set())

    def test_device_open_failure_removes_private_audio(self):
        with patch("sounddevice.RawInputStream", side_effect=RuntimeError("device busy")):
            with self.assertRaisesRegex(RuntimeError, "device busy"):
                self.capture.run(lambda: None)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_selected_device_never_silently_falls_back(self):
        prefs = Preferences(microphone="USB", host_api="ALSA")
        with patch("dictadesk.audio.input.microphones", return_value=[]):
            with self.assertRaises(RuntimeError):
                resolve(prefs)
        with patch("dictadesk.audio.input.microphones", return_value=[(2, "USB", "ALSA")]):
            self.assertEqual(resolve(prefs), 2)
        with patch(
            "dictadesk.audio.input.microphones",
            return_value=[(2, "USB", "ALSA"), (3, "USB", "ALSA")],
        ):
            with self.assertRaises(RuntimeError):
                resolve(prefs)

    def test_readiness_check_does_not_open_stream(self):
        with (
            patch("sounddevice.RawInputStream") as stream,
            patch("sounddevice.query_devices"),
            patch("sounddevice.check_input_settings") as settings,
        ):
            check(Preferences())
        stream.assert_not_called()
        settings.assert_called_once()


class PreviewTests(unittest.TestCase):
    def test_only_speech_followed_by_pause_produces_preview(self):
        preview = PausePreview(0.3, 0.01)
        preview.add(bytes(16000))
        self.assertTrue(preview.pending.empty())
        preview.add(array("h", [5000] * 8000).tobytes())
        self.assertTrue(preview.pending.empty())
        preview.add(bytes(9600))
        self.assertEqual(len(preview.pending.get_nowait()), 41600)
        preview.add(bytes(9600))
        self.assertTrue(preview.pending.empty())

    def test_queue_retains_only_latest_snapshot(self):
        preview = PausePreview(0.3, 0.01)
        for _ in range(3):
            preview.add(array("h", [5000] * 8000).tobytes())
            preview.add(bytes(9600))
        self.assertEqual(preview.pending.qsize(), 1)
        self.assertEqual(len(preview.pending.get_nowait()), 76800)


class CloudTests(unittest.TestCase):
    def test_cloud_multipart_file_and_fields(self):
        from dictadesk.audio.worker import cloud

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.wav"
            path.write_bytes(b"RIFFfixture")
            requests = []

            class Response:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    pass

                def read(self):
                    return b'{"text":"hello"}'

            def fetch(req, **kwargs):
                requests.append(req)
                return Response()

            with patch("urllib.request.urlopen", side_effect=fetch):
                result = cloud(
                    {
                        "path": str(path),
                        "key": "test-only",
                        "model": "gpt-transcribe",
                        "language": "en",
                    }
                )
            self.assertEqual(result, "hello")
            self.assertIn(b"RIFFfixture", requests[0].data)
            self.assertIn(b'name="languages[]"', requests[0].data)
            self.assertNotIn(b'name="language"', requests[0].data)
            self.assertEqual(requests[0].get_header("Authorization"), "Bearer test-only")

    def test_http_failure_does_not_echo_secret_or_body(self):
        from dictadesk.audio.worker import cloud
        import urllib.error

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.wav"
            path.write_bytes(b"audio")
            with patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.HTTPError("private-url", 401, "test-secret", {}, None),
            ):
                with self.assertRaises(RuntimeError) as caught:
                    cloud({"path": str(path), "key": "test-secret", "model": "gpt-transcribe"})
            self.assertNotIn("test-secret", str(caught.exception))
            self.assertIn("401", str(caught.exception))

    def test_current_multipart_contract_and_automatic_language(self):
        import io
        from email.parser import BytesParser
        from email.policy import default
        from dictadesk.audio.worker import cloud

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.wav"
            path.write_bytes(b"RIFFfixture")
            for language, metadata in (("", []), ("zh-tw", [{"code": "zh"}])):
                import json

                response = io.BytesIO(
                    json.dumps({"text": "hello", "languages": metadata}).encode()
                )
                with patch("urllib.request.urlopen", return_value=response) as fetch:
                    self.assertEqual(
                        cloud(
                            {
                                "path": str(path),
                                "model": "gpt-transcribe",
                                "key": "fixture-key",
                                "language": language,
                            }
                        ),
                        "hello",
                    )
                request = fetch.call_args.args[0]
                self.assertEqual(
                    request.full_url, "https://api.openai.com/v1/audio/transcriptions"
                )
                self.assertEqual(request.get_method(), "POST")
                message = BytesParser(policy=default).parsebytes(
                    (
                        "Content-Type: " + request.get_header("Content-type") + "\r\n\r\n"
                    ).encode()
                    + request.data
                )
                parts = {
                    part.get_param("name", header="content-disposition"): part
                    for part in message.iter_parts()
                }
                expected = {"file", "model", "response_format"} | (
                    {"languages[]"} if language else set()
                )
                self.assertEqual(set(parts), expected)
                self.assertEqual(parts["model"].get_payload(decode=True), b"gpt-transcribe")
                self.assertEqual(parts["response_format"].get_payload(decode=True), b"json")
                self.assertEqual(parts["file"].get_content_type(), "audio/wav")
                self.assertEqual(parts["file"].get_payload(decode=True), b"RIFFfixture")
                if language:
                    self.assertEqual(
                        parts["languages[]"].get_payload(decode=True), language.encode()
                    )

    def test_invalid_response_is_sanitized(self):
        import io
        from dictadesk.audio.worker import cloud

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.wav"
            path.write_bytes(b"RIFFfixture")
            for body in (
                b"private-response",
                b"[]",
                b"{}",
                b'{"text": null}',
                b'{"text": ["private"]}',
            ):
                with (
                    self.subTest(body=body),
                    patch("urllib.request.urlopen", return_value=io.BytesIO(body)),
                ):
                    with self.assertRaisesRegex(RuntimeError, "invalid transcript") as caught:
                        cloud(
                            {
                                "path": str(path),
                                "model": "gpt-transcribe",
                                "key": "fixture-secret",
                            }
                        )
                    self.assertNotIn("private", str(caught.exception))
                    self.assertNotIn("fixture-secret", str(caught.exception))

    def test_network_failure_is_sanitized(self):
        import urllib.error
        from dictadesk.audio.worker import cloud

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.wav"
            path.write_bytes(b"RIFFfixture")
            for error in (
                urllib.error.URLError("fixture-secret"),
                TimeoutError("fixture-secret"),
            ):
                with patch("urllib.request.urlopen", side_effect=error):
                    with self.assertRaisesRegex(
                        RuntimeError, "Could not reach OpenAI"
                    ) as caught:
                        cloud(
                            {
                                "path": str(path),
                                "model": "gpt-transcribe",
                                "key": "fixture-secret",
                            }
                        )
                    self.assertNotIn("fixture-secret", str(caught.exception))

    def test_unsupported_model_never_uploads(self):
        from dictadesk.audio.worker import cloud

        for model in (
            "whisper-1",
            "gpt-4o-transcribe",
            "gpt-4o-mini-transcribe",
            "gpt-live-transcribe",
        ):
            with self.subTest(model=model), patch("urllib.request.urlopen") as fetch:
                with self.assertRaisesRegex(ValueError, "supported remote transcription model"):
                    cloud({"path": "/does/not/exist", "model": model, "key": "fixture"})
                fetch.assert_not_called()

    def test_remote_worker_cancellation_stops_pending_upload(self):
        import sys
        import threading
        from dictadesk.audio.service import Recognizer

        # Real child process, fixture transport: no network or audio leaves the test.
        script = """
import urllib.request, threading
from dictadesk.audio.worker import main
urllib.request.urlopen = lambda *a, **kw: threading.Event().wait()
main()
"""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.wav"
            path.write_bytes(b"RIFFfixture")
            worker = Recognizer([sys.executable, "-u", "-c", script])
            cancel = threading.Event()
            timer = threading.Timer(0.3, cancel.set)
            timer.start()
            try:
                with self.assertRaises(InterruptedError):
                    worker.request(
                        {
                            "op": "cloud",
                            "path": str(path),
                            "model": "gpt-transcribe",
                            "key": "fixture",
                        },
                        cancel,
                    )
                self.assertIsNone(worker.process)
            finally:
                timer.cancel()
                timer.join()
                worker.close()
