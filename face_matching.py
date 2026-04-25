"""
Advanced face matching system with metric learning, adaptive thresholding,
multi-reference comparison, confidence scoring, and FAISS indexing.
"""

import cv2
import numpy as np
from typing import List, Tuple, Optional, Dict
from dataclasses import dataclass
import threading

from config import MATCHING_CFG, LIVENESS_CFG, EMBEDDING_CFG

from face_embedding import IdentityGallery, EmbeddingVector

try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False

try:
    import mediapipe as mp
    MEDIAPIPE_AVAILABLE = True
except ImportError:
    MEDIAPIPE_AVAILABLE = False


@dataclass
class MatchResult:
    """Result of matching a probe embedding against the gallery."""
    matched: bool
    identity_id: Optional[str]
    identity_name: Optional[str]
    distance: float
    confidence: float
    top_matches: List[Tuple[str, float]]  # (identity_id, distance) list
    liveness_score: float = 1.0
    is_live: bool = True


@dataclass
class LivenessResult:
    """Anti-spoofing analysis result."""
    is_live: bool
    liveness_score: float
    texture_score: float
    replay_score: float


class AdaptiveThreshold:
    """
    Computes per-identity adaptive thresholds based on intra-class variance
    and global population statistics.
    """

    def __init__(self, base_threshold: float = MATCHING_CFG.base_threshold):
        self.base_threshold = base_threshold
        self._identity_thresholds: Dict[str, float] = {}
        self._global_mean = 0.0
        self._global_std = 0.0
        self._lock = threading.Lock()

    def update_statistics(self, identity_variances: List[Tuple[str, float]]):
        """
        Update global statistics from identity intra-class variances.
        """
        with self._lock:
            variances = [v for _, v in identity_variances]
            if variances:
                self._global_mean = float(np.mean(variances))
                self._global_std = float(np.std(variances)) + 1e-10

            for identity_id, var in identity_variances:
                # Higher variance -> higher threshold (more lenient)
                # Lower variance -> lower threshold (stricter)
                z_score = (var - self._global_mean) / self._global_std
                adaptive = self.base_threshold + 0.05 * z_score
                adaptive = float(np.clip(adaptive, 0.35, 0.75))
                self._identity_thresholds[identity_id] = adaptive

    def get_threshold(self, identity_id: Optional[str] = None) -> float:
        """
        Get threshold for a specific identity, or fall back to base.
        """
        with self._lock:
            if identity_id and identity_id in self._identity_thresholds:
                return self._identity_thresholds[identity_id]
            return self.base_threshold


class FaissIndex:
    """
    FAISS-based approximate nearest neighbor index for scalable matching.
    Falls back to linear search if FAISS is not available.
    """

    def __init__(self, embedding_dim: int = EMBEDDING_CFG.embedding_dim):
        self.embedding_dim = embedding_dim
        self._index = None
        self._identity_map: List[str] = []  # Maps index position to identity_id
        self._lock = threading.Lock()

    def build(self, embeddings: List[Tuple[str, np.ndarray]]):
        """
        Build index from list of (identity_id, embedding_vector).
        """
        with self._lock:
            if not embeddings:
                self._index = None
                return

            self._identity_map = [eid for eid, _ in embeddings]
            vectors = np.array([emb for _, emb in embeddings], dtype=np.float32)

            if FAISS_AVAILABLE and MATCHING_CFG.use_faiss_index:
                # Use IndexFlatIP for exact cosine similarity on normalized vectors
                self._index = faiss.IndexFlatIP(self.embedding_dim)
                self._index.add(vectors)
            else:
                self._index = vectors  # Fallback: store as numpy array

    def search(
        self,
        query: np.ndarray,
        k: int = MATCHING_CFG.top_k_matches,
    ) -> List[Tuple[str, float]]:
        """
        Search for k nearest neighbors. Returns list of (identity_id, distance).
        Distance is cosine distance (1 - cosine_similarity).
        """
        with self._lock:
            if self._index is None or len(self._identity_map) == 0:
                return []

            query = query.astype(np.float32).reshape(1, -1)

            if isinstance(self._index, np.ndarray):
                # Fallback linear search
                similarities = np.dot(self._index, query.T).flatten()
                top_k_idx = np.argsort(similarities)[::-1][:k]
                return [
                    (self._identity_map[i], float(1 - similarities[i]))
                    for i in top_k_idx
                ]
            else:
                # FAISS search
                similarities, indices = self._index.search(query, k)
                return [
                    (self._identity_map[i], float(1 - similarities[0][j]))
                    for j, i in enumerate(indices[0]) if i >= 0
                ]


class LivenessDetector:
    """
    Anti-spoofing detection using texture analysis and other heuristics.
    """

    def analyze(self, image: np.ndarray) -> LivenessResult:
        """
        Analyze image for signs of spoofing (print, replay attack).
        """
        if not LIVENESS_CFG.enabled:
            return LivenessResult(is_live=True, liveness_score=1.0, texture_score=1.0, replay_score=1.0)

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        # Texture analysis using Local Binary Patterns variance
        texture_score = self._lbp_variance(gray) if LIVENESS_CFG.texture_analysis else 1.0

        # Replay detection: check for moire patterns / unnatural frequencies
        replay_score = self._check_moire(gray) if LIVENESS_CFG.texture_analysis else 1.0

        # Combine scores
        liveness_score = min(texture_score, replay_score)
        is_live = liveness_score >= LIVENESS_CFG.lbp_threshold

        return LivenessResult(
            is_live=is_live,
            liveness_score=liveness_score,
            texture_score=texture_score,
            replay_score=replay_score,
        )

    def _lbp_variance(self, gray: np.ndarray) -> float:
        """Compute LBP pattern variance as texture richness indicator."""
        h, w = gray.shape
        if h < 3 or w < 3:
            return 0.0

        lbp = np.zeros_like(gray, dtype=np.uint8)
        for i in range(1, h - 1):
            for j in range(1, w - 1):
                center = gray[i, j]
                binary = (
                    (gray[i-1, j-1] >= center) << 7 |
                    (gray[i-1, j] >= center) << 6 |
                    (gray[i-1, j+1] >= center) << 5 |
                    (gray[i, j+1] >= center) << 4 |
                    (gray[i+1, j+1] >= center) << 3 |
                    (gray[i+1, j] >= center) << 2 |
                    (gray[i+1, j-1] >= center) << 1 |
                    (gray[i, j-1] >= center)
                )
                lbp[i, j] = binary

        hist, _ = np.histogram(lbp[1:-1, 1:-1], bins=256, range=(0, 256))
        hist = hist / (hist.sum() + 1e-10)
        variance = float(np.var(hist))
        # Normalize: high variance = rich texture = likely live
        return min(1.0, variance * 50)

    def _check_moire(self, gray: np.ndarray) -> float:
        """Detect moire patterns typical of screen replay attacks."""
        # Simple FFT-based detection
        f = np.fft.fft2(gray)
        fshift = np.fft.fftshift(f)
        magnitude = np.abs(fshift)

        # Check for periodic spikes
        center_y, center_x = magnitude.shape[0] // 2, magnitude.shape[1] // 2
        roi = magnitude[center_y-10:center_y+10, center_x-10:center_x+10]
        peak_ratio = roi.max() / (magnitude.mean() + 1e-10)

        # High peak ratio suggests artificial periodicity (screen)
        score = 1.0 - min(1.0, (peak_ratio - 5.0) / 20.0)
        return max(0.0, score)


class FaceMatcher:
    """
    Main matching orchestrator combining indexing, adaptive thresholds,
    multi-reference voting, and confidence calibration.
    """

    def __init__(self):
        self.index = FaissIndex()
        self.threshold_manager = AdaptiveThreshold()
        self.liveness_detector = LivenessDetector()
        self._galleries: Dict[str, IdentityGallery] = {}
        self._lock = threading.Lock()

    def enroll(self, galleries: List[IdentityGallery]):
        """
        Enroll identities into the matching system.
        Builds FAISS index and computes adaptive thresholds.
        """
        with self._lock:
            self._galleries = {g.identity_id: g for g in galleries}

            # Build flat list of all embeddings for index
            flat_embeddings: List[Tuple[str, np.ndarray]] = []
            identity_variances: List[Tuple[str, float]] = []

            for gallery in galleries:
                for emb in gallery.embeddings:
                    flat_embeddings.append((gallery.identity_id, emb.vector))
                identity_variances.append((gallery.identity_id, gallery.intra_class_variance))

            self.index.build(flat_embeddings)
            self.threshold_manager.update_statistics(identity_variances)

    def match(
        self,
        probe_embedding: np.ndarray,
        probe_image: Optional[np.ndarray] = None,
    ) -> MatchResult:
        """
        Match a probe embedding against enrolled identities.

        Returns comprehensive match result with confidence score.
        """
        with self._lock:
            # Liveness check
            liveness = LivenessResult(is_live=True, liveness_score=1.0, texture_score=1.0, replay_score=1.0)
            if probe_image is not None and LIVENESS_CFG.enabled:
                liveness = self.liveness_detector.analyze(probe_image)

            # Search for nearest neighbors
            top_matches = self.index.search(probe_embedding, k=MATCHING_CFG.top_k_matches)

            if not top_matches:
                return MatchResult(
                    matched=False,
                    identity_id=None,
                    identity_name=None,
                    distance=1.0,
                    confidence=0.0,
                    top_matches=[],
                    liveness_score=liveness.liveness_score,
                    is_live=liveness.is_live,
                )

            best_id, best_dist = top_matches[0]

            # Multi-reference voting if enabled
            if MATCHING_CFG.multi_reference_voting and best_id in self._galleries:
                gallery = self._galleries[best_id]
                # Compare against all embeddings in gallery
                distances = [
                    float(1 - np.dot(probe_embedding, emb.vector))
                    for emb in gallery.embeddings
                ]
                distances.sort()

                if MATCHING_CFG.voting_strategy == "best":
                    best_dist = distances[0]
                elif MATCHING_CFG.voting_strategy == "weighted_average":
                    weights = [1.0 / (d + 0.01) for d in distances[:5]]
                    best_dist = float(np.average(distances[:5], weights=weights))
                else:  # majority / mean
                    best_dist = float(np.mean(distances[:5]))

            # Adaptive threshold
            threshold = self.threshold_manager.get_threshold(best_id)
            matched = best_dist <= threshold

            # Confidence scoring
            confidence = self._compute_confidence(
                best_dist=best_dist,
                threshold=threshold,
                top_matches=top_matches,
                gallery=self._galleries.get(best_id),
            )

            # Final decision incorporates liveness
            if liveness.is_live:
                verified = matched and (confidence >= MATCHING_CFG.min_confidence_for_match)
            else:
                verified = False

            gallery_name = self._galleries[best_id].name if best_id in self._galleries else None

            return MatchResult(
                matched=verified,
                identity_id=best_id if verified else None,
                identity_name=gallery_name if verified else None,
                distance=best_dist,
                confidence=confidence,
                top_matches=top_matches,
                liveness_score=liveness.liveness_score,
                is_live=liveness.is_live,
            )

    def verify_identity(
        self,
        identity_id: str,
        probe_embedding: np.ndarray,
        probe_image: Optional[np.ndarray] = None,
    ) -> MatchResult:
        """
        Verify if a probe matches a specific identity (1:1 comparison).
        """
        with self._lock:
            liveness = LivenessResult(is_live=True, liveness_score=1.0, texture_score=1.0, replay_score=1.0)
            if probe_image is not None and LIVENESS_CFG.enabled:
                liveness = self.liveness_detector.analyze(probe_image)

            if identity_id not in self._galleries:
                return MatchResult(
                    matched=False,
                    identity_id=identity_id,
                    identity_name=None,
                    distance=1.0,
                    confidence=0.0,
                    top_matches=[],
                    liveness_score=liveness.liveness_score,
                    is_live=liveness.is_live,
                )

            gallery = self._galleries[identity_id]

            # Multi-reference comparison
            distances = [
                float(1 - np.dot(probe_embedding, emb.vector))
                for emb in gallery.embeddings
            ]
            distances.sort()

            if MATCHING_CFG.voting_strategy == "best":
                best_dist = distances[0]
            elif MATCHING_CFG.voting_strategy == "weighted_average":
                weights = [1.0 / (d + 0.01) for d in distances[:5]]
                best_dist = float(np.average(distances[:5], weights=weights))
            else:
                best_dist = float(np.mean(distances[:5]))

            threshold = self.threshold_manager.get_threshold(identity_id)
            matched = best_dist <= threshold

            # Confidence for 1:1
            confidence = self._compute_confidence_1v1(
                best_dist=best_dist,
                threshold=threshold,
                gallery=gallery,
            )

            if liveness.is_live:
                verified = matched and (confidence >= MATCHING_CFG.min_confidence_for_match)
            else:
                verified = False

            return MatchResult(
                matched=verified,
                identity_id=identity_id if verified else None,
                identity_name=gallery.name if verified else None,
                distance=best_dist,
                confidence=confidence,
                top_matches=[(identity_id, d) for d in distances[:5]],
                liveness_score=liveness.liveness_score,
                is_live=liveness.is_live,
            )

    def _compute_confidence(
        self,
        best_dist: float,
        threshold: float,
        top_matches: List[Tuple[str, float]],
        gallery: Optional[IdentityGallery],
    ) -> float:
        """
        Compute calibrated confidence score [0, 1].
        """
        # Factor 1: Distance relative to threshold (lower = better)
        if best_dist <= threshold:
            dist_conf = 1.0 - (best_dist / threshold) * 0.5
        else:
            dist_conf = max(0.0, 0.5 - (best_dist - threshold) * 2)

        # Factor 2: Margin between top match and second best
        margin_conf = 1.0
        if len(top_matches) >= 2:
            margin = top_matches[1][1] - best_dist
            margin_conf = min(1.0, margin * 5.0)

        # Factor 3: Gallery quality (lower intra-class variance = more reliable)
        quality_conf = 1.0
        if gallery is not None:
            quality_conf = 1.0 - min(0.3, gallery.intra_class_variance)

        # Weighted combination
        confidence = 0.5 * dist_conf + 0.3 * margin_conf + 0.2 * quality_conf
        return float(np.clip(confidence, 0.0, 1.0))

    def _compute_confidence_1v1(
        self,
        best_dist: float,
        threshold: float,
        gallery: IdentityGallery,
    ) -> float:
        """Confidence for 1:1 verification."""
        if best_dist <= threshold:
            dist_conf = 1.0 - (best_dist / threshold) * 0.5
        else:
            dist_conf = max(0.0, 0.5 - (best_dist - threshold) * 2)

        quality_conf = 1.0 - min(0.3, gallery.intra_class_variance)

        # For 1v1, also check consistency across gallery embeddings
        consistency = 1.0 - min(0.3, np.std([best_dist]))

        confidence = 0.5 * dist_conf + 0.3 * quality_conf + 0.2 * consistency
        return float(np.clip(confidence, 0.0, 1.0))


class VideoLivenessDetector:
    """
    Video-based liveness detection using blink detection,
    temporal motion analysis, and frame consistency.
    """

    def __init__(self):
        self._mp_face_mesh = None
        if MEDIAPIPE_AVAILABLE:
            self._mp_face_mesh = mp.solutions.face_mesh.FaceMesh(
                static_image_mode=False,
                max_num_faces=1,
                refine_landmarks=True,
                min_detection_confidence=0.5,
            )

    def analyze_video(self, frames: List[np.ndarray]) -> LivenessResult:
        """
        Analyze a sequence of frames for liveness.
        Returns LivenessResult with is_live flag.
        """
        if len(frames) < 3:
            # Fallback to single-frame liveness
            detector = LivenessDetector()
            return detector.analyze(frames[0]) if frames else LivenessResult(
                is_live=False, liveness_score=0.0, texture_score=0.0, replay_score=0.0
            )

        # 1. Temporal motion analysis
        motion_score = self._compute_motion_score(frames)

        # 2. Blink detection
        blink_score = self._detect_blinks(frames)

        # 3. Texture analysis on a few sampled frames
        texture_scores = []
        detector = LivenessDetector()
        for frame in frames[::max(1, len(frames) // 3)]:
            lr = detector.analyze(frame)
            texture_scores.append(lr.texture_score)
        avg_texture = float(np.mean(texture_scores)) if texture_scores else 1.0

        # 4. Replay detection
        replay_scores = []
        for frame in frames[::max(1, len(frames) // 3)]:
            lr = detector.analyze(frame)
            replay_scores.append(lr.replay_score)
        avg_replay = float(np.mean(replay_scores)) if replay_scores else 1.0

        # Combine scores
        # Motion is critical - static image should fail
        liveness_score = min(motion_score, blink_score, avg_texture, avg_replay)

        # If there's no motion, force fail
        if motion_score < LIVENESS_CFG.motion_threshold * 10:  # scale up
            is_live = False
            liveness_score = min(liveness_score, motion_score)
        else:
            is_live = liveness_score >= LIVENESS_CFG.lbp_threshold

        return LivenessResult(
            is_live=is_live,
            liveness_score=liveness_score,
            texture_score=avg_texture,
            replay_score=avg_replay,
        )

    def _compute_motion_score(self, frames: List[np.ndarray]) -> float:
        """Compute average optical flow / frame difference score."""
        if len(frames) < 2:
            return 0.0

        scores = []
        for i in range(1, len(frames)):
            prev_gray = cv2.cvtColor(frames[i - 1], cv2.COLOR_BGR2GRAY)
            curr_gray = cv2.cvtColor(frames[i], cv2.COLOR_BGR2GRAY)

            # Frame difference
            diff = cv2.absdiff(prev_gray, curr_gray)
            diff_ratio = np.mean(diff) / 255.0
            scores.append(diff_ratio)

        avg_motion = float(np.mean(scores))
        return avg_motion

    def _detect_blinks(self, frames: List[np.ndarray]) -> float:
        """Detect blinks using eye aspect ratio changes."""
        if not self._mp_face_mesh or len(frames) < 4:
            return 1.0  # Can't detect, assume OK

        ear_values = []
        for frame in frames:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = self._mp_face_mesh.process(rgb)
            if not results.multi_face_landmarks:
                continue

            landmarks = results.multi_face_landmarks[0].landmark
            h, w = frame.shape[:2]

            left_ear = self._eye_aspect_ratio(landmarks, w, h, is_left=True)
            right_ear = self._eye_aspect_ratio(landmarks, w, h, is_left=False)
            ear = (left_ear + right_ear) / 2.0
            ear_values.append(ear)

        if len(ear_values) < 4:
            return 1.0

        # Detect blink: EAR drops below threshold then rises back
        blink_count = 0
        ear_threshold = 0.2
        below_threshold = False

        for ear in ear_values:
            if ear < ear_threshold:
                below_threshold = True
            elif below_threshold and ear >= ear_threshold:
                blink_count += 1
                below_threshold = False

        if blink_count >= LIVENESS_CFG.min_blinks_required:
            return 1.0
        else:
            # Partial score based on EAR variance
            ear_variance = float(np.var(ear_values))
            return min(1.0, ear_variance * 20)  # Some eye movement is better than none

    def _eye_aspect_ratio(self, landmarks, w: int, h: int, is_left: bool = True) -> float:
        """Compute EAR for one eye using MediaPipe landmarks."""
        if is_left:
            # Left eye indices in MediaPipe
            p1 = (landmarks[33].x * w, landmarks[33].y * h)
            p2 = (landmarks[160].x * w, landmarks[160].y * h)
            p3 = (landmarks[158].x * w, landmarks[158].y * h)
            p4 = (landmarks[133].x * w, landmarks[133].y * h)
            p5 = (landmarks[153].x * w, landmarks[153].y * h)
            p6 = (landmarks[144].x * w, landmarks[144].y * h)
        else:
            # Right eye indices
            p1 = (landmarks[362].x * w, landmarks[362].y * h)
            p2 = (landmarks[385].x * w, landmarks[385].y * h)
            p3 = (landmarks[387].x * w, landmarks[387].y * h)
            p4 = (landmarks[263].x * w, landmarks[263].y * h)
            p5 = (landmarks[373].x * w, landmarks[373].y * h)
            p6 = (landmarks[380].x * w, landmarks[380].y * h)

        def dist(a, b):
            return np.linalg.norm(np.array(a) - np.array(b))

        # Vertical distances
        v1 = dist(p2, p6)
        v2 = dist(p3, p5)
        # Horizontal distance
        h_dist = dist(p1, p4)

        if h_dist == 0:
            return 0.0
        return (v1 + v2) / (2.0 * h_dist)

