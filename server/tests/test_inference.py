import json
import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

from server.inference import InferenceRequest, OllamaInferenceAdapter, inference_adapter, inference_mode


class OllamaInferenceTests(TestCase):
    def test_ollama_adapter_sends_frames_and_returns_answer(self) -> None:
        response = MagicMock()
        response.read.return_value = json.dumps({"message": {"content": "A runner appears [[00:03]]."}}).encode()
        response.__enter__.return_value = response
        request = InferenceRequest(
            path=Path("video.mp4"),
            question="What happens?",
            history=[],
            duration=10.0,
            fallback=lambda _question, _duration: "fallback",
            transcript=[{"start_seconds": 2, "end_seconds": 4, "text": "Let's go"}],
        )

        with patch.dict(os.environ, {"OLLAMA_VISION_MODEL": "qwen3-vl:2b-instruct"}, clear=False), patch(
            "server.inference.sample_video_frames", return_value=[(3.0, "encoded-frame")]
        ), patch("server.inference.urllib.request.urlopen", return_value=response) as urlopen:
            answer = OllamaInferenceAdapter().answer(request)

        self.assertEqual(answer, "A runner appears [[00:03]].")
        sent = json.loads(urlopen.call_args.args[0].data.decode())
        self.assertEqual(sent["messages"][0]["images"], ["encoded-frame"])
        self.assertFalse(sent["think"])
        self.assertEqual(sent["options"]["num_predict"], 160)
        self.assertIn("image 1 = 3.0s", sent["messages"][0]["content"])

    def test_ollama_is_selected_without_paid_api(self) -> None:
        with patch.dict(os.environ, {"OLLAMA_VISION_MODEL": "qwen3-vl:2b-instruct", "OPENAI_API_KEY": ""}, clear=False):
            self.assertEqual(inference_mode(), "local-vision")
            self.assertIsInstance(inference_adapter(), OllamaInferenceAdapter)
