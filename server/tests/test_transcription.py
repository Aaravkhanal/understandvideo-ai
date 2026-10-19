from unittest import TestCase

from server.transcription import _friendly_speaker_labels, _speaker_for_segment


class SpeakerAssignmentTests(TestCase):
    def test_segment_uses_speaker_with_largest_overlap(self) -> None:
        turns = [(0.0, 2.0, "SPEAKER_00"), (2.0, 8.0, "SPEAKER_01")]

        self.assertEqual(_speaker_for_segment(1.5, 4.0, turns), "SPEAKER_01")

    def test_segment_without_overlap_has_no_speaker(self) -> None:
        self.assertIsNone(_speaker_for_segment(10.0, 12.0, [(0.0, 4.0, "SPEAKER_00")]))

    def test_labels_follow_first_speaking_order(self) -> None:
        turns = [(5.0, 6.0, "SPEAKER_B"), (1.0, 2.0, "SPEAKER_A"), (7.0, 8.0, "SPEAKER_A")]

        self.assertEqual(
            _friendly_speaker_labels(turns),
            {"SPEAKER_A": "Speaker 1", "SPEAKER_B": "Speaker 2"},
        )
