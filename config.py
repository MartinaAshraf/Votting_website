"""
Fast/Lightweight configuration for face verification.
Optimized for speed over accuracy - single model, no augmentation, no FAISS.
"""

from pathlib import Path
from dataclasses import dataclass
from typing import Tuple

BASE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class ModelConfig:
    primary_model: str = "Facenet"  # Lightweight model
    ensemble_models: Tuple[str, ...] = ("Facenet",)  # Single model only
    ensemble_weights: Tuple[float, ...] = (1.0,)
    detector_backend: str = "skip"
    enforce_detection: bool = False
    normalization: str = "base"


@dataclass(frozen=True)
class PreprocessingConfig:
    target_size: Tuple[int, int] = (112, 112)
    align_faces: bool = True
    apply_denoising: bool = False  # Skip for speed
    apply_clahe: bool = False  # Skip for speed
    blur_threshold: float = 30.0  # Lower threshold (less strict)
    brightness_min: float = 20.0
    brightness_max: float = 250.0
    min_face_size_ratio: float = 0.10  # Smaller faces allowed


@dataclass(frozen=True)
class EmbeddingConfig:
    use_augmentation: bool = False  # DISABLED - major speedup
    augmentation_angles: Tuple[float, ...] = (0,)
    augmentation_brightness: Tuple[float, ...] = (1.0,)
    augmentation_contrast: Tuple[float, ...] = (1.0,)
    num_augmentations: int = 1
    embedding_dim: int = 128
    normalize_embeddings: bool = True
    use_pca_whitening: bool = False  # DISABLED


@dataclass(frozen=True)
class MatchingConfig:
    base_threshold: float = 0.60
    adaptive_threshold: bool = False  # DISABLED
    threshold_margin: float = 0.05
    use_faiss_index: bool = False  # DISABLED - use simple cosine
    faiss_index_type: str = ""
    faiss_nprobe: int = 1
    top_k_matches: int = 1
    multi_reference_voting: bool = False  # DISABLED
    voting_strategy: str = "best"
    min_confidence_for_match: float = 0.60


@dataclass(frozen=True)
class LivenessConfig:
    enabled: bool = True  # ENABLED for video liveness verification
    texture_analysis: bool = True
    depth_consistency: bool = True
    blink_detection: bool = True
    lbp_threshold: float = 0.5
    replay_threshold: float = 0.5
    min_blinks_required: int = 0
    motion_threshold: float = 0.01
    temporal_consistency_threshold: float = 0.5


@dataclass(frozen=True)
class VideoConfig:
    max_upload_bytes: int = 10 * 1024 * 1024  # 10MB
    min_duration_seconds: float = 2.0
    max_duration_seconds: float = 5.0
    target_duration_seconds: float = 3.0
    frame_sample_rate: int = 5  # 5 fps for better temporal analysis
    min_frames_required: int = 3
    allowed_content_types: Tuple[str, ...] = (
        "video/mp4", "video/avi", "video/x-msvideo", 
        "video/webm", "video/quicktime"
    )


@dataclass(frozen=True)
class SystemConfig:
    base_dir: Path = BASE_DIR
    captures_dir: Path = BASE_DIR / "captured_faces"
    captures_api_dir: Path = BASE_DIR / "captured_faces_api"
    payloads_dir: Path = BASE_DIR / "verification_payloads"
    voter_faces_dir: Path = BASE_DIR / "voter_faces"
    web_dir: Path = BASE_DIR / "web"
    database_json: Path = BASE_DIR / "database.json"
    max_upload_bytes: int = 5 * 1024 * 1024
    allowed_content_types: Tuple[str, ...] = ("image/jpeg", "image/png", "image/webp")
    rate_limit_requests: int = 20  # More lenient
    rate_limit_window: int = 60
    replay_ttl_seconds: int = 120
    log_level: str = "INFO"


# Global singletons
MODEL_CFG = ModelConfig()
PREPROCESS_CFG = PreprocessingConfig()
EMBEDDING_CFG = EmbeddingConfig()
MATCHING_CFG = MatchingConfig()
LIVENESS_CFG = LivenessConfig()
VIDEO_CFG = VideoConfig()
SYSTEM_CFG = SystemConfig()