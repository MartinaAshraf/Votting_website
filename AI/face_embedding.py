"""
Advanced face embedding extraction with multi-model ensemble,
augmentation pipeline, and per-identity embedding gallery management.
"""

import cv2
import numpy as np
from typing import List, Tuple, Optional
from dataclasses import dataclass
import threading

from deepface import DeepFace

from config import MODEL_CFG, EMBEDDING_CFG, PREPROCESS_CFG
from face_preprocessing import FacePreprocessor, PreprocessingResult


@dataclass
class EmbeddingVector:
    """A single embedding with metadata."""
    vector: np.ndarray
    model_name: str
    augmentation_type: str = "none"  # e.g., "original", "rotated_5", "bright_1.2"
    quality_score: float = 1.0


@dataclass
class IdentityGallery:
    """
    A collection of embeddings for one identity.
    Stores multiple embeddings to handle variations.
    """
    identity_id: str
    name: str
    embeddings: List[EmbeddingVector]
    # Centroid for fast approximate matching
    centroid: Optional[np.ndarray] = None
    # Intra-class variance for adaptive thresholding
    intra_class_variance: float = 0.0


class EmbeddingExtractor:
    """
    Extracts face embeddings using multiple models and augmentation.
    Produces robust embeddings invariant to lighting, angle, expression,
    and partial occlusion.
    """

    def __init__(self):
        self.preprocessor = FacePreprocessor()
        self._model_cache: dict = {}
        self._cache_lock = threading.Lock()

    def extract_single(
        self,
        image: np.ndarray,
        model_name: Optional[str] = None,
    ) -> EmbeddingVector:
        """
        Extract a single embedding from a face image.

        Args:
            image: Preprocessed face image (BGR)
            model_name: Which DeepFace model to use; defaults to primary

        Returns:
            EmbeddingVector with normalized embedding
        """
        model = model_name or MODEL_CFG.primary_model

        # DeepFace represent
        result = DeepFace.represent(
            img_path=image,
            model_name=model,
            detector_backend=MODEL_CFG.detector_backend,
            enforce_detection=MODEL_CFG.enforce_detection,
            normalization=MODEL_CFG.normalization,
        )

        embedding = np.array(result[0]["embedding"], dtype=np.float32)

        if EMBEDDING_CFG.normalize_embeddings:
            embedding = embedding / (np.linalg.norm(embedding) + 1e-10)

        return EmbeddingVector(
            vector=embedding,
            model_name=model,
            augmentation_type="original",
            quality_score=1.0,
        )

    def extract_with_augmentation(
        self,
        image: np.ndarray,
        model_name: Optional[str] = None,
    ) -> List[EmbeddingVector]:
        """
        Extract multiple embeddings with augmentation for robustness.

        Generates augmented variants (rotation, brightness, contrast)
        and extracts embeddings from each.
        """
        model = model_name or MODEL_CFG.primary_model
        embeddings: List[EmbeddingVector] = []

        # Original
        orig = self.extract_single(image, model)
        embeddings.append(orig)

        if not EMBEDDING_CFG.use_augmentation:
            return embeddings

        # Augmented variants
        aug_configs = self._generate_augmentations()

        for i, (angle, brightness, contrast) in enumerate(aug_configs):
            aug_img = self._apply_augmentation(image, angle, brightness, contrast)
            emb = self.extract_single(aug_img, model)
            emb.augmentation_type = f"aug_{i}_a{angle}_b{brightness}_c{contrast}"
            emb.quality_score = 0.95  # Slightly lower weight for augmented
            embeddings.append(emb)

        return embeddings

    def extract_ensemble(
        self,
        image: np.ndarray,
    ) -> List[EmbeddingVector]:
        """
        Extract embeddings from multiple models and fuse them.
        Returns a list where the first element is the ensemble centroid.
        """
        all_embeddings: List[EmbeddingVector] = []

        for model, weight in zip(MODEL_CFG.ensemble_models, MODEL_CFG.ensemble_weights):
            embs = self.extract_with_augmentation(image, model)
            for emb in embs:
                emb.quality_score *= weight
            all_embeddings.extend(embs)

        # Compute ensemble centroid (weighted average of model centroids)
        vectors = [e.vector * e.quality_score for e in all_embeddings]
        weights = [e.quality_score for e in all_embeddings]
        centroid = np.sum(vectors, axis=0) / (np.sum(weights) + 1e-10)
        centroid = centroid / (np.linalg.norm(centroid) + 1e-10)

        ensemble_emb = EmbeddingVector(
            vector=centroid,
            model_name="ensemble_centroid",
            augmentation_type="fused",
            quality_score=1.0,
        )

        # Return centroid first, then all individual embeddings
        return [ensemble_emb] + all_embeddings

    def build_gallery(
        self,
        identity_id: str,
        name: str,
        image_paths: List[str],
    ) -> IdentityGallery:
        """
        Build a comprehensive embedding gallery for an identity
        from multiple reference images (different angles, ages, etc.).
        """
        all_embeddings: List[EmbeddingVector] = []

        for img_path in image_paths:
            image = cv2.imread(img_path)
            if image is None:
                continue

            # Preprocess
            preprocessed = self.preprocessor.preprocess(image)
            if preprocessed.quality_report.recommended_action == "reject":
                continue

            face_img = preprocessed.image

            # Extract ensemble embeddings
            embs = self.extract_ensemble(face_img)
            all_embeddings.extend(embs)

        if not all_embeddings:
            raise ValueError(f"Could not extract any valid embeddings for {identity_id}")

        # Compute gallery centroid
        vectors = [e.vector for e in all_embeddings]
        centroid = np.mean(vectors, axis=0)
        centroid = centroid / (np.linalg.norm(centroid) + 1e-10)

        # Compute intra-class variance (average distance from centroid)
        distances = [1 - np.dot(centroid, v) for v in vectors]
        intra_var = float(np.mean(distances))

        return IdentityGallery(
            identity_id=identity_id,
            name=name,
            embeddings=all_embeddings,
            centroid=centroid,
            intra_class_variance=intra_var,
        )

    def build_gallery_from_image(
        self,
        identity_id: str,
        name: str,
        image: np.ndarray,
    ) -> IdentityGallery:
        """
        Build a gallery from a single image with augmentation.
        Useful when only one reference photo is available.
        """
        preprocessed = self.preprocessor.preprocess(image)
        face_img = preprocessed.image

        # Extract many augmented variants
        all_embeddings: List[EmbeddingVector] = []
        embs = self.extract_ensemble(face_img)
        all_embeddings.extend(embs)

        if not all_embeddings:
            raise ValueError(f"Could not extract embeddings for {identity_id}")

        vectors = [e.vector for e in all_embeddings]
        centroid = np.mean(vectors, axis=0)
        centroid = centroid / (np.linalg.norm(centroid) + 1e-10)

        distances = [1 - np.dot(centroid, v) for v in vectors]
        intra_var = float(np.mean(distances))

        return IdentityGallery(
            identity_id=identity_id,
            name=name,
            embeddings=all_embeddings,
            centroid=centroid,
            intra_class_variance=intra_var,
        )

    def _generate_augmentations(self) -> List[Tuple[float, float, float]]:
        """Generate augmentation parameter combinations."""
        import random
        random.seed(42)  # Reproducible augmentations

        configs = []
        angles = list(EMBEDDING_CFG.augmentation_angles)
        brightnesses = list(EMBEDDING_CFG.augmentation_brightness)
        contrasts = list(EMBEDDING_CFG.augmentation_contrast)

        for _ in range(EMBEDDING_CFG.num_augmentations):
            configs.append((
                random.choice(angles),
                random.choice(brightnesses),
                random.choice(contrasts),
            ))
        return configs

    def _apply_augmentation(
        self,
        image: np.ndarray,
        angle: float,
        brightness: float,
        contrast: float,
    ) -> np.ndarray:
        """Apply rotation, brightness, and contrast adjustments."""
        h, w = image.shape[:2]
        center = (w // 2, h // 2)

        # Rotation
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(image, M, (w, h), borderMode=cv2.BORDER_REPLICATE)

        # Brightness and contrast
        adjusted = cv2.convertScaleAbs(rotated, alpha=contrast, beta=(brightness - 1.0) * 50)

        return adjusted

