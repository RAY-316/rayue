import unittest

from app.api import grok


class TalkingPhotoTests(unittest.TestCase):
    def test_builds_talking_photo_task_payload(self) -> None:
        builder = getattr(grok, "_talking_photo_task_payload", None)
        self.assertIsNotNone(builder, "talking photo task payload builder is missing")

        payload = builder(
            generation_id="grok_123",
            image_url="https://example.com/photo.jpg",
            audio_url="https://example.com/audio.mp3",
            prompt="  Smile and speak naturally.  ",
        )

        self.assertEqual(payload["webhookOverride"], "")
        self.assertEqual(payload["priority"], 5)
        self.assertEqual(payload["algorithmFrom"], "video_generation")
        self.assertEqual(payload["algorithmType"], "talking_photo")
        self.assertEqual(payload["taskName"], "talking_photo")
        self.assertEqual(payload["queueName"], grok.settings.talking_photo_queue)
        self.assertEqual(payload["data"]["talking_photo_url"], "https://example.com/photo.jpg")
        self.assertEqual(payload["data"]["input_audio"], "https://example.com/audio.mp3")
        self.assertEqual(payload["data"]["input_video"], "")
        self.assertEqual(payload["data"]["prompt"], "Smile and speak naturally.")
        self.assertEqual(payload["data"]["max_audio_duration"], 60)
        self.assertEqual(payload["data"]["fps"], 25)
        self.assertEqual(payload["data"]["parent_task_id"], grok.settings.talking_photo_parent_task_id)
        self.assertTrue(str(payload["data"]["video_id"]).startswith("rayue-grok_123"))

    def test_talking_photo_prompt_defaults_when_blank(self) -> None:
        builder = getattr(grok, "_talking_photo_task_payload", None)
        self.assertIsNotNone(builder, "talking photo task payload builder is missing")

        payload = builder(
            generation_id="grok_456",
            image_url="https://example.com/photo.jpg",
            audio_url="https://example.com/audio.mp3",
            prompt=" ",
        )

        self.assertEqual(payload["data"]["prompt"], "A person talking")

    def test_normalizes_talking_photo_status(self) -> None:
        normalizer = getattr(grok, "_normalize_talking_photo_status", None)
        self.assertIsNotNone(normalizer, "talking photo status normalizer is missing")

        self.assertEqual(normalizer(1), "running")
        self.assertEqual(normalizer(2), "running")
        self.assertEqual(normalizer(3), "succeeded")
        self.assertEqual(normalizer(4), "failed")
        self.assertEqual(normalizer(6), "failed")
        self.assertEqual(normalizer(-1), "failed")
        self.assertEqual(normalizer("RUNNING"), "running")
        self.assertEqual(normalizer("FINISHED"), "succeeded")

    def test_extracts_talking_photo_video_url(self) -> None:
        extractor = getattr(grok, "_talking_photo_video_url", None)
        self.assertIsNotNone(extractor, "talking photo video URL extractor is missing")

        self.assertEqual(
            extractor({"data": {"video_url": "https://cdn.example.com/result.mp4"}}),
            "https://cdn.example.com/result.mp4",
        )
        self.assertEqual(extractor({"video_url": "https://cdn.example.com/result.mp4"}), "https://cdn.example.com/result.mp4")
        self.assertIsNone(extractor({"data": {}}))


if __name__ == "__main__":
    unittest.main()
