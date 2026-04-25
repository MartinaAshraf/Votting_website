"""
Face preprocessing pipeline: detection, alignment, quality assessment.
"""

import cv2
import numpy as np
from dataclasses import dataclass
from typing import List, Optional, Tuple

try:
    import mediapipe as mp
    MEDIAPIPE_AVAILABLE = True
except ImportError:
    MEDIAPIPE_AVAILABLE = False


@dataclass
class ImageQualityReport:
    is_acceptable: bool = True
    recommended_action: str = "proceed"  # or "reject"
    issues: Optional[List[str]] = None

    def __post_init__(self):
        if self.issues is None:
            self.issues = []


@dataclass
class PreprocessingResult:
    image: np.ndarray
    quality_report: ImageQualityReport
    landmarks: Optional[np.ndarray] = None


class FacePreprocessor:
    """
    Detects face, aligns it, and checks image quality.
    Uses MediaPipe face detection when available, falls back to OpenCV Haar.
    """

    def __init__(self, target_size: Tuple[int, int] = (112, 112)):
        self.target_size = target_size
        self._mp_face_detection = None
        self._mp_face_mesh = None
        if MEDIAPIPE_AVAILABLE:
            self._mp_face_detection = mp.solutions.face_detection.FaceDetection(
                model_selection=1, min_detection_confidence=0.5
            )
            self._mp_face_mesh = mp.solutions.face_mesh.FaceMesh(
                static_image_mode=True,
                max_num_faces=1,
                refine_landmarks=True,
                min_detection_confidence=0.5,
            )

    def _detect_landmarks(self, image: np.ndarray) -> Optional[np.ndarray]:
        """Return 2D face landmarks as Nx2 array, or None if no face."""
        if not MEDIAPIPE_AVAILABLE:
            return self._detect_landmarks_opencv(image)

        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        h, w = image.shape[:2]

        # Try face mesh first for detailed landmarks
        if self._mp_face_mesh:
            results = self._mp_face_mesh.process(rgb)
            if results.multi_face_landmarks:
                landmarks = []
                for lm in results.multi_face_landmarks[0].landmark:
                    landmarks.append([lm.x * w, lm.y * h])
                return np.array(landmarks, dtype=np.float32)

        # Fall back to face detection bounding box corners as landmarks
        if self._mp_face_detection:
            results = self._mp_face_detection.process(rgb)
            if results.detections:
                det = results.detections[0]
                bbox = det.location_data.relative_bounding_box
                x_min = int(bbox.xmin * w)
                y_min = int(bbox.ymin * h)
                box_w = int(bbox.width * w)
                box_h = int(bbox.height * h)
                return np.array(
                    [
                        [x_min, y_min],
                        [x_min + box_w, y_min],
                        [x_min + box_w, y_min + box_h],
                        [x_min, y_min + box_h],
                    ],
                    dtype=np.float32,
                )

        return None

    def _detect_landmarks_opencv(self, image: np.ndarray) -> Optional[np.ndarray]:
        """Fallback OpenCV Haar face detector."""
        face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, 1.1, 4)
        if len(faces) == 0:
            return None
        x, y, w, h = faces[0]
        return np.array(
            [
                [x, y],
                [x + w, y],
                [x + w, y + h],
                [x, y + h],
            ],
            dtype=np.float32,
        )

    def preprocess(self, image: np.ndarray) -> PreprocessingResult:
        """
        Detect face, align, enhance quality, and resize to target_size.
        """
        issues: List[str] = []
        landmarks = self._detect_landmarks(image)

        if landmarks is None:
            return PreprocessingResult(
                image=image,
                quality_report=ImageQualityReport(
                    is_acceptable=False,
                    recommended_action="reject",
                    issues=["No face detected"],
                ),
                landmarks=None,
            )

        # Quality checks
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        lap_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        brightness = float(np.mean(gray))

        if lap_var < 80:
            issues.append("Image is too blurry")
        if brightness < 40:
            issues.append("Image is too dark")
        if brightness > 230:
            issues.append("Image is too bright")

        # Crop to face region using landmarks bbox
        x_min, y_min = landmarks.min(axis=0)
        x_max, y_max = landmarks.max(axis=0)
        margin = 0.2
        box_w = x_max - x_min
        box_h = y_max - y_min
        x_min = max(0, int(x_min - margin * box_w))
        y_min = max(0, int(y_min - margin * box_h))
        x_max = min(image.shape[1], int(x_max + margin * box_w))
        y_max = min(image.shape[0], int(y_max + margin * box_h))

        face_crop = image[y_min:y_max, x_min:x_max]
        if face_crop.size == 0:
            return PreprocessingResult(
                image=image,
                quality_report=ImageQualityReport(
                    is_acceptable=False,
                    recommended_action="reject",
                    issues=["Invalid face crop"],
                ),
                landmarks=landmarks,
            )

        # CLAHE for contrast
        lab = cv2.cvtColor(face_crop, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l = clahe.apply(l)
        face_crop = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)

        # Denoise
        face_crop = cv2.fastNlMeansDenoisingColored(face_crop, None, 10, 10, 7, 21)

        # Resize
        face_resized = cv2.resize(face_crop, self.target_size)

        is_acceptable = len(issues) == 0
        return PreprocessingResult(
            image=face_resized,
            quality_report=ImageQualityReport(
                is_acceptable=is_acceptable,
                recommended_action="proceed" if is_acceptable else "reject",
                issues=issues,
            ),
            landmarks=landmarks,
        )

