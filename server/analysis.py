from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class AnalysisError(Exception):
    pass


@dataclass(frozen=True)
class ExtractedScene:
    position: int
    start_seconds: float
    end_seconds: float
    label: str
    thumbnail_path: Path
    detection_method: str


def extract_scene_thumbnails(
    video_path: Path,
    output_root: Path,
    video_id: str,
    count: int = 4,
) -> list[ExtractedScene]:
    """Detect strong visual cuts, then fill sparse timelines with neutral reference frames."""
    try:
        import cv2
    except ImportError as exc:
        raise AnalysisError("Video frame extraction is not installed") from exc

    capture = cv2.VideoCapture(str(video_path))
    try:
        if not capture.isOpened():
            raise AnalysisError("The video could not be decoded for timeline extraction")
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
        if frame_count <= 0 or fps <= 0:
            raise AnalysisError("The video does not expose a readable timeline")

        scene_count = min(8, max(1, count), frame_count)
        duration = frame_count / fps
        scan_step = max(1, frame_count // 600)
        previous_histogram = None
        candidates: list[tuple[float, float]] = []
        for frame_index in range(0, frame_count, scan_step):
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = capture.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            histogram = cv2.calcHist([gray], [0], None, [32], [0, 256])
            cv2.normalize(histogram, histogram)
            if previous_histogram is not None:
                score = float(cv2.compareHist(previous_histogram, histogram, cv2.HISTCMP_BHATTACHARYYA))
                if score >= 0.32:
                    candidates.append((score, frame_index / fps))
            previous_histogram = histogram

        minimum_gap = max(0.75, duration / max(2, scene_count * 2))
        selected: list[tuple[float, str]] = [(0.0, "opening-frame")]
        for _, timestamp in sorted(candidates, reverse=True):
            if len(selected) >= scene_count:
                break
            if all(abs(timestamp - existing) >= minimum_gap for existing, _ in selected):
                selected.append((timestamp, "histogram-cut-v1"))

        fallback_ratios = [index / max(1, scene_count - 1) for index in range(scene_count)]
        for ratio in fallback_ratios:
            if len(selected) >= scene_count:
                break
            timestamp = min(max(0.0, duration - 0.05), duration * ratio)
            if all(abs(timestamp - existing) >= minimum_gap * 0.6 for existing, _ in selected):
                selected.append((timestamp, "uniform-fallback"))

        # A strong cut can sit close to a uniform reference point. Fill any
        # remaining slots from distinct source frames so callers receive the
        # requested number whenever the video actually contains enough frames.
        if len(selected) < scene_count:
            fallback_frames = [
                round(index * max(0, frame_count - 1) / max(1, scene_count - 1))
                for index in range(scene_count)
            ]
            for frame_index in fallback_frames:
                if len(selected) >= scene_count:
                    break
                timestamp = frame_index / fps
                if all(abs(timestamp - existing) >= (0.5 / fps) for existing, _ in selected):
                    selected.append((timestamp, "uniform-fallback"))

        timestamps_with_methods = sorted(selected, key=lambda item: item[0])[:scene_count]
        scene_dir = output_root / "derived" / video_id
        scene_dir.mkdir(parents=True, exist_ok=True)
        scenes: list[ExtractedScene] = []

        for position, (timestamp, detection_method) in enumerate(timestamps_with_methods):
            frame_index = min(frame_count - 1, max(0, round(timestamp * fps)))
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = capture.read()
            if not ok:
                continue
            height, width = frame.shape[:2]
            scale = min(1.0, 720 / max(width, height))
            if scale < 1:
                frame = cv2.resize(frame, (max(1, int(width * scale)), max(1, int(height * scale))))
            thumbnail_path = scene_dir / f"scene-{position + 1}.jpg"
            encoded = cv2.imwrite(
                str(thumbnail_path),
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), 82],
            )
            if not encoded:
                continue
            next_timestamp = timestamps_with_methods[position + 1][0] if position + 1 < len(timestamps_with_methods) else duration
            scenes.append(
                ExtractedScene(
                    position=position,
                    start_seconds=round(timestamp, 2),
                    end_seconds=round(max(timestamp, next_timestamp), 2),
                    label=f"Scene {position + 1}",
                    thumbnail_path=thumbnail_path,
                    detection_method=detection_method,
                )
            )

        if not scenes:
            raise AnalysisError("No readable video frames were found")
        return scenes
    finally:
        capture.release()
