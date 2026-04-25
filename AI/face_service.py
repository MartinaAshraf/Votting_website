"""
Enhanced Face Verification Service — orchestrates preprocessing,
embedding extraction, and advanced matching with confidence scoring.
"""

from dataclasses import dataclass
from pathlib import Path
import os
import tempfile
import threading
from typing import Optional, List

import cv2
import numpy as np

from config import MATCHING_CFG, PREPROCESS_CFG, SYSTEM_CFG, VIDEO_CFG
from face_preprocessing import FacePreprocessor, ImageQualityReport
from face_embedding import EmbeddingExtractor, IdentityGallery
from face_matching import FaceMatcher, MatchResult, LivenessResult, VideoLivenessDetector


@dataclass
class VerificationResult:
    """Final verification result for API consumers."""
    matched: bool
    distance: float
    confidence: float
    identity_id: Optional[str] = None
    identity_name: Optional[str] = None
    liveness: str = "LIVE"
    quality_acceptable: bool = True
    quality_issues: List[str] = None

    def __post_init__(self):
        if self.quality_issues is None:
            self.quality_issues = []


@dataclass
class FrameAssessment:
    """Assessment of whether a frame is ready for verification."""
    ready: bool
    message: str
    quality: Optional[ImageQualityReport] = None


@dataclass
class FaceMetrics:
    """Face detection metrics for UI guidance."""
    detected: bool
    center_x_ratio: Optional[float] = None
    center_y_ratio: Optional[float] = None
    width_ratio: Optional[float] = None
    height_ratio: Optional[float] = None


@dataclass
class Identity:
    """Legacy identity dataclass for backward compatibility."""
    id: str
    name: str
    embedding: List[float]


class FaceVerificationService:
    """
    Production-ready face verification service.
    Integrates preprocessing, multi-model embedding, and advanced matching.
    """

    def __init__(
        self,
        identities: List[Identity],
        match_threshold: float = MATCHING_CFG.base_threshold,
    ):
        self.match_threshold = match_threshold
        self.preprocessor = FacePreprocessor()
        self.extractor = EmbeddingExtractor()
        self.matcher = FaceMatcher()

        # Build galleries from legacy Identity objects
        self._build_galleries(identities)

        self._runtime_lock = threading.Lock()

    def _build_galleries(self, identities: List[Identity]):
        """Convert legacy Identity list to IdentityGallery and enroll."""
        galleries: List[IdentityGallery] = []
        for ident in identities:
            if ident.embedding:
                # Legacy embedding provided directly
                gallery = IdentityGallery(
                    identity_id=ident.id,
                    name=ident.name,
                    embeddings=[],
                    centroid=np.array(ident.embedding, dtype=np.float32),
                    intra_class_variance=0.05,
                )
                galleries.append(gallery)
        if galleries:
            self.matcher.enroll(galleries)

    def verify_image(self, frame: np.ndarray) -> VerificationResult:
        """
        Verify a face image against all enrolled identities (1:N search).
        """
        with self._runtime_lock:
            # Preprocess
            preprocessed = self.preprocessor.preprocess(frame)
            quality = preprocessed.quality_report

            if not quality.is_acceptable and quality.recommended_action == "reject":
                return VerificationResult(
                    matched=False,
                    distance=1.0,
                    confidence=0.0,
                    quality_acceptable=False,
                    quality_issues=quality.issues,
                )

            face_img = preprocessed.image

            # Extract ensemble embedding (use centroid)
            embs = self.extractor.extract_ensemble(face_img)
            probe_emb = embs[0].vector  # ensemble centroid

            # Match
            result = self.matcher.match(probe_emb, probe_image=frame)

            return VerificationResult(
                matched=result.matched,
                distance=result.distance,
                confidence=result.confidence,
                identity_id=result.identity_id,
                identity_name=result.identity_name,
                liveness="LIVE" if result.is_live else "SPOOF",
                quality_acceptable=quality.is_acceptable,
                quality_issues=quality.issues,
            )

    def verify_image_bytes(self, image_bytes: bytes) -> VerificationResult:
        """Verify from raw image bytes."""
        frame = self.decode_image_bytes(image_bytes)
        return self.verify_image(frame)

    def verify_identity_bytes(
        self,
        identity_id: str,
        image_bytes: bytes,
    ) -> VerificationResult:
        """
        Verify if image matches a specific identity (1:1 verification).
        """
        with self._runtime_lock:
            frame = self.decode_image_bytes(image_bytes)
            preprocessed = self.preprocessor.preprocess(frame)
            quality = preprocessed.quality_report

            if not quality.is_acceptable and quality.recommended_action == "reject":
                return VerificationResult(
                    matched=False,
                    distance=1.0,
                    confidence=0.0,
                    identity_id=identity_id,
                    quality_acceptable=False,
                    quality_issues=quality.issues,
                )

            face_img = preprocessed.image
            embs = self.extractor.extract_ensemble(face_img)
            probe_emb = embs[0].vector

            result = self.matcher.verify_identity(identity_id, probe_emb, probe_image=frame)

            return VerificationResult(
                matched=result.matched,
                distance=result.distance,
                confidence=result.confidence,
                identity_id=result.identity_id,
                identity_name=result.identity_name,
                liveness="LIVE" if result.is_live else "SPOOF",
                quality_acceptable=quality.is_acceptable,
                quality_issues=quality.issues,
            )

    def verify_video_bytes(
        self,
        identity_id: str,
        video_bytes: bytes,
    ) -> VerificationResult:
        """
        Verify a video against a specific identity.
        Extracts frames, checks video liveness, and matches best frame.
        """
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp:
            tmp.write(video_bytes)
            tmp_path = tmp.name

        try:
            frames = self._extract_video_frames(tmp_path)
            if len(frames) < VIDEO_CFG.min_frames_required:
                return VerificationResult(
                    matched=False,
                    distance=1.0,
                    confidence=0.0,
                    identity_id=identity_id,
                    liveness="SPOOF",
                    quality_acceptable=False,
                    quality_issues=[f"Too few frames extracted: {len(frames)}"],
                )

            # Video liveness detection
            video_liveness = VideoLivenessDetector()
            liveness_result = video_liveness.analyze_video(frames)

            if not liveness_result.is_live:
                return VerificationResult(
                    matched=False,
                    distance=1.0,
                    confidence=0.0,
                    identity_id=identity_id,
                    liveness="SPOOF",
                    quality_acceptable=True,
                    quality_issues=["Video liveness check failed"],
                )

            # Find best frame for matching
            best_result = None
            best_confidence = -1.0

            for frame in frames:
                try:
                    preprocessed = self.preprocessor.preprocess(frame)
                    if not preprocessed.quality_report.is_acceptable:
                        continue

                    face_img = preprocessed.image
                    embs = self.extractor.extract_ensemble(face_img)
                    probe_emb = embs[0].vector

                    result = self.matcher.verify_identity(identity_id, probe_emb, probe_image=frame)

                    if result.confidence > best_confidence:
                        best_confidence = result.confidence
                        best_result = result
                except Exception:
                    continue

            if best_result is None:
                return VerificationResult(
                    matched=False,
                    distance=1.0,
                    confidence=0.0,
                    identity_id=identity_id,
                    liveness="LIVE" if liveness_result.is_live else "SPOOF",
                    quality_acceptable=False,
                    quality_issues=["No valid face found in video frames"],
                )

            return VerificationResult(
                matched=best_result.matched,
                distance=best_result.distance,
                confidence=best_result.confidence,
                identity_id=best_result.identity_id,
                identity_name=best_result.identity_name,
                liveness="LIVE" if liveness_result.is_live else "SPOOF",
                quality_acceptable=True,
                quality_issues=[],
            )
        finally:
            os.unlink(tmp_path)

    def _extract_video_frames(self, video_path: str) -> List[np.ndarray]:
        """Extract frames from video at configured sample rate."""
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []

        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 30.0

        frame_interval = int(fps / VIDEO_CFG.frame_sample_rate)
        if frame_interval < 1:
            frame_interval = 1

        frames = []
        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if frame_idx % frame_interval == 0:
                frames.append(frame)
            frame_idx += 1

        cap.release()
        return frames

    def assess_and_verify_image_bytes(
        self,
        image_bytes: bytes,
    ) -> tuple[FrameAssessment, Optional[VerificationResult]]:
        """
        First assess frame quality, then verify if ready.
        """
        frame = self.decode_image_bytes(image_bytes)
        preprocessed = self.preprocessor.preprocess(frame)
        quality = preprocessed.quality_report

        if not quality.is_acceptable:
            return FrameAssessment(
                ready=False,
                message=f"Quality issue: {', '.join(quality.issues)}",
                quality=quality,
            ), None

        result = self.verify_image(frame)
        return FrameAssessment(ready=True, message="Face detected and verified", quality=quality), result

    def analyze_image_bytes(self, image_bytes: bytes) -> FaceMetrics:
        """
        Analyze image for face detection metrics (used by UI for positioning).
        """
        frame = self.decode_image_bytes(image_bytes)
        landmarks = self.preprocessor._detect_landmarks(frame)

        if landmarks is None:
            return FaceMetrics(detected=False)

        h, w = frame.shape[:2]
        x_min, y_min = landmarks.min(axis=0)
        x_max, y_max = landmarks.max(axis=0)
        face_w = max(1, x_max - x_min)
        face_h = max(1, y_max - y_min)
        center_x = x_min + face_w / 2
        center_y = y_min + face_h / 2

        return FaceMetrics(
            detected=True,
            center_x_ratio=float(center_x / w),
            center_y_ratio=float(center_y / h),
            width_ratio=float(face_w / w),
            height_ratio=float(face_h / h),
        )

    def decode_image_bytes(self, image_bytes: bytes) -> np.ndarray:
        """Decode image bytes to OpenCV BGR array."""
        arr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("Invalid image bytes")
        return img

    def _get_embedding_from_array(self, frame: np.ndarray) -> List[float]:
        """Legacy helper: extract embedding from frame."""
        preprocessed = self.preprocessor.preprocess(frame)
        face_img = preprocessed.image
        embs = self.extractor.extract_ensemble(face_img)
        return embs[0].vector.tolist()

    def _get_embedding_from_path(self, image_path: Path | str) -> List[float]:
        """Legacy helper: extract embedding from file path."""
        frame = cv2.imread(str(image_path))
        if frame is None:
            raise ValueError(f"Could not read image: {image_path}")
        return self._get_embedding_from_array(frame)

    def with_identities(self, identities: List[Identity]) -> "FaceVerificationService":
        """Create a new service instance with different identities."""
        return FaceVerificationService(
            identities=identities,
            match_threshold=self.match_threshold,
        )

    # Enrollment API for new identities
    def enroll_identity(
        self,
        identity_id: str,
        name: str,
        image_paths: List[str],
    ) -> IdentityGallery:
        """Enroll a new identity from multiple reference images."""
        gallery = self.extractor.build_gallery(identity_id, name, image_paths)
        self.matcher.enroll(list(self.matcher._galleries.values()) + [gallery])
        return gallery

    def enroll_identity_from_image(
        self,
        identity_id: str,
        name: str,
        image: np.ndarray,
    ) -> IdentityGallery:
        """Enroll from a single image (with augmentation)."""
        gallery = self.extractor.build_gallery_from_image(identity_id, name, image)
        self.matcher.enroll(list(self.matcher._galleries.values()) + [gallery])
        return gallery
