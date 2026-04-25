from pathlib import Path

import hashlib
import logging
import re
import tempfile
import threading
from collections import defaultdict
from datetime import datetime
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import json
import os
import time
import mysql.connector
import cv2
import numpy as np

from face_service import FaceVerificationService, Identity
from config import VIDEO_CFG


BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
DB_DATA_PATH = BASE_DIR / "database.json"
CAPTURES_DIR = BASE_DIR / "captured_faces_api"
PAYLOADS_DIR = BASE_DIR / "verification_payloads"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
app = FastAPI(title="Secure Face Verification")
app.mount("/web", StaticFiles(directory=WEB_DIR), name="web")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
_service_cache: FaceVerificationService | None = None
_service_cache_mtime: float | None = None
_embedding_service: FaceVerificationService | None = None
_service_lock = threading.Lock()
_embedding_service_lock = threading.Lock()
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_VIDEO_TYPES = set(VIDEO_CFG.allowed_content_types)
IDENTITY_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,64}$")
NATIONAL_ID_PATTERN = re.compile(r"^\d{14}$")
logger = logging.getLogger(__name__)
MYSQL_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DATABASE", ""),
}
IMAGE_SEARCH_DIRS = (
    BASE_DIR,
    BASE_DIR / "voter_faces",
    BASE_DIR / "captured_faces_api",
)
_mysql_users_database: str | None = None
_mysql_users_database_lock = threading.Lock()


class RateLimiter:
    def __init__(self, max_requests=10, window=60):
        self.max_requests = max_requests
        self.window = window
        self.requests = defaultdict(list)
        self._lock = threading.Lock()

    def is_allowed(self, ip):
        with self._lock:
            now = time.time()
            self.requests[ip] = [t for t in self.requests[ip] if now - t < self.window]
            if len(self.requests[ip]) >= self.max_requests:
                return False
            self.requests[ip].append(now)
            return True


class ReplayProtector:
    def __init__(self, ttl_seconds: int = 120):
        self.ttl_seconds = ttl_seconds
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def register(self, client_key: str, payload: bytes) -> bool:
        now = time.time()
        fingerprint = hashlib.sha256(payload).hexdigest()
        key = f"{client_key}:{fingerprint}"
        with self._lock:
            self._seen = {
                saved_key: ts
                for saved_key, ts in self._seen.items()
                if now - ts < self.ttl_seconds
            }
            if key in self._seen:
                return False
            self._seen[key] = now
            return True


limiter = RateLimiter()
replay_protector = ReplayProtector()


def _validate_identity_record(record: dict, index: int) -> Identity:
    if "embedding" not in record or not isinstance(record["embedding"], list):
        raise ValueError(f"Identity record #{index} must store an embedding list.")

    if any(field in record for field in ("image", "image_path", "raw_image", "face_image")):
        raise ValueError(
            f"Identity record #{index} must store facial embeddings instead of raw images."
        )

    identity_id = str(record.get("id", "")).strip()
    if not IDENTITY_ID_PATTERN.fullmatch(identity_id):
        raise ValueError(f"Identity record #{index} has an invalid id.")

    name = str(record.get("name", "")).strip()
    if not name:
        raise ValueError(f"Identity record #{index} is missing a valid name.")

    embedding = []
    for value in record["embedding"]:
        if not isinstance(value, (int, float)):
            raise ValueError(f"Identity record #{index} has a non-numeric embedding value.")
        embedding.append(float(value))

    if not embedding and not record.get("image_name"):
        raise ValueError(f"Identity record #{index} has no embedding and no image_name.")

    return Identity(id=identity_id, name=name, embedding=embedding)


async def _read_validated_image(image: UploadFile) -> bytes:
    if not image.content_type or image.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Please upload a JPEG, PNG, or WEBP face image.",
        )

    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Uploaded image is empty")
    if len(image_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image is too large")
    return image_bytes


async def _read_validated_video(video: UploadFile) -> bytes:
    if not video.content_type or video.content_type not in ALLOWED_VIDEO_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Please upload a valid video file. Allowed types: {', '.join(ALLOWED_VIDEO_TYPES)}",
        )

    video_bytes = await video.read()
    if not video_bytes:
        raise HTTPException(status_code=400, detail="Uploaded video is empty")
    if len(video_bytes) > VIDEO_CFG.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Video is too large (max 50MB)")
    return video_bytes


def _validate_video_duration(video_bytes: bytes) -> float:
    """Validate video duration and return it in seconds."""
    with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp:
        tmp.write(video_bytes)
        tmp_path = tmp.name

    try:
        cap = cv2.VideoCapture(tmp_path)
        if not cap.isOpened():
            cap.release()
            raise HTTPException(status_code=400, detail="Invalid video file: cannot open")

        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()

        if fps <= 0:
            raise HTTPException(status_code=400, detail="Invalid video file: cannot determine frame rate")

        duration = frame_count / fps

        if duration < VIDEO_CFG.min_duration_seconds:
            raise HTTPException(
                status_code=400,
                detail=f"Video too short: {duration:.1f}s. Minimum required: {VIDEO_CFG.min_duration_seconds}s"
            )
        if duration > VIDEO_CFG.max_duration_seconds:
            raise HTTPException(
                status_code=400,
                detail=f"Video too long: {duration:.1f}s. Maximum allowed: {VIDEO_CFG.max_duration_seconds}s"
            )

        return duration
    finally:
        os.unlink(tmp_path)


def _client_identifier(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _enforce_request_security(request: Request, payload: str | bytes) -> None:
    client_id = _client_identifier(request)
    if not limiter.is_allowed(client_id):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    payload_bytes = payload if isinstance(payload, bytes) else payload.encode("utf-8")
    if not replay_protector.register(client_id, payload_bytes):
        raise HTTPException(status_code=409, detail="Replay attack detected")


def _format_verification_response(result) -> dict[str, float | bool | str]:
    verified = bool(result.matched) and result.liveness == "LIVE"
    return {
        "status": "matched" if verified else "not_matched",
        "message": "مطابق" if verified else "غير مطابق",
        "match": bool(result.matched),
        "similarity_score": round(1 - float(result.distance), 6),
        "liveness": result.liveness,
        "confidence": float(result.confidence),
        "verified": verified,
        "quality_acceptable": result.quality_acceptable,
        "quality_issues": result.quality_issues or [],
    }


def _validate_identity_id(identity_id: str) -> str:
    cleaned = identity_id.strip()
    if not IDENTITY_ID_PATTERN.fullmatch(cleaned):
        raise HTTPException(status_code=400, detail="Invalid identity id format")
    return cleaned


def _validate_national_id(national_id: str) -> str:
    cleaned = national_id.strip()
    if not NATIONAL_ID_PATTERN.fullmatch(cleaned):
        raise HTTPException(status_code=400, detail="Invalid national_id format")
    return cleaned


def _resolve_image_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path

    candidates = [BASE_DIR / path]
    if path.name != str(path):
        candidates.append(BASE_DIR / path.name)

    for directory in IMAGE_SEARCH_DIRS:
        candidates.append(directory / path.name)

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return candidates[0]


def _get_embedding_service() -> FaceVerificationService:
    global _embedding_service
    if _embedding_service is None:
        with _embedding_service_lock:
            if _embedding_service is None:
                _embedding_service = FaceVerificationService([])
    return _embedding_service


def _mysql_server_config() -> dict[str, str]:
    return {
        "host": MYSQL_CONFIG["host"],
        "user": MYSQL_CONFIG["user"],
        "password": MYSQL_CONFIG["password"],
    }


def _database_has_required_users_table(database_name: str) -> bool:
    conn = mysql.connector.connect(**_mysql_server_config(), database=database_name)
    cursor = conn.cursor()
    try:
        cursor.execute("SHOW TABLES LIKE 'users'")
        if cursor.fetchone() is None:
            return False

        cursor.execute(f"DESCRIBE `{database_name}`.`users`")
        columns = {row[0] for row in cursor.fetchall()}
        required_columns = {"id", "full_name", "national_id", "img"}
        return required_columns.issubset(columns)
    finally:
        cursor.close()
        conn.close()


def _list_users_databases() -> list[str]:
    global _mysql_users_database
    if _mysql_users_database is not None:
        return [_mysql_users_database]

    with _mysql_users_database_lock:
        if _mysql_users_database is not None:
            return [_mysql_users_database]

        preferred_names: list[str] = []
        configured = MYSQL_CONFIG.get("database", "").strip()
        if configured:
            preferred_names.append(configured)
        preferred_names.extend(["final", "mysql"])

        server_conn = mysql.connector.connect(**_mysql_server_config())
        cursor = server_conn.cursor()
        try:
            cursor.execute("SHOW DATABASES")
            available_databases = [row[0] for row in cursor.fetchall()]
        finally:
            cursor.close()
            server_conn.close()

        ordered_candidates: list[str] = []
        for name in preferred_names + available_databases:
            if name not in ordered_candidates:
                ordered_candidates.append(name)

        valid_databases: list[str] = []
        for database_name in ordered_candidates:
            try:
                if _database_has_required_users_table(database_name):
                    valid_databases.append(database_name)
            except mysql.connector.Error:
                continue

        if valid_databases:
            _mysql_users_database = valid_databases[0]
            logger.info("Detected MySQL users databases: %s", ", ".join(valid_databases))

        return valid_databases


def _fetch_user_by_national_id_from_json(national_id: str) -> dict[str, str] | None:
    if not DB_DATA_PATH.exists():
        return None

    with open(DB_DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("database.json must contain a list of identities.")

    for record in data:
        record_national_id = str(record.get("id", "")).strip()
        if record_national_id != national_id:
            continue

        return {
            "id": record_national_id,
            "full_name": str(record.get("name", "")).strip() or record_national_id,
            "national_id": record_national_id,
            "img": str(record.get("image_name", "")).strip(),
        }

    return None


def _fetch_user_by_national_id(national_id: str) -> dict[str, str] | None:
    try:
        databases = _list_users_databases()
        if not databases:
            return _fetch_user_by_national_id_from_json(national_id)

        for database_name in databases:
            conn = mysql.connector.connect(**_mysql_server_config(), database=database_name)
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(
                    """
                    SELECT id, full_name, national_id, img
                    FROM users
                    WHERE national_id = %s
                    LIMIT 1
                    """,
                    (national_id,),
                )
                row = cursor.fetchone()
                if not row:
                    continue
                return {
                    "id": str(row["id"]),
                    "full_name": str(row["full_name"]).strip(),
                    "national_id": str(row["national_id"]).strip(),
                    "img": str(row["img"] or "").strip(),
                }
            finally:
                cursor.close()
                conn.close()

        return _fetch_user_by_national_id_from_json(national_id)
    except mysql.connector.Error as exc:
        if getattr(exc, "errno", None) == 1146:
            logger.warning(
                "MySQL table `users` was not found in database `%s`; falling back to database.json",
                MYSQL_CONFIG.get("database") or "auto-detect",
            )
            return _fetch_user_by_national_id_from_json(national_id)
        raise


def _save_camera_capture(image_bytes: bytes) -> Path:
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    file_name = f"capture_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}.jpg"
    image_path = CAPTURES_DIR / file_name
    image_path.write_bytes(image_bytes)
    return image_path


def _save_video_thumbnail(frames: list, national_id: str) -> Path:
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    file_name = f"video_thumb_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{national_id}_{uuid4().hex[:8]}.jpg"
    thumb_path = CAPTURES_DIR / file_name
    if frames:
        cv2.imwrite(str(thumb_path), frames[len(frames) // 2])
    return thumb_path


def _extract_thumbnail_from_video_bytes(video_bytes: bytes, national_id: str) -> Path:
    """Extract a middle frame from video bytes and save as thumbnail."""
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    file_name = f"video_thumb_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{national_id}_{uuid4().hex[:8]}.jpg"
    thumb_path = CAPTURES_DIR / file_name

    with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp:
        tmp.write(video_bytes)
        tmp_path = tmp.name

    try:
        cap = cv2.VideoCapture(tmp_path)
        if not cap.isOpened():
            # Fallback: save empty placeholder
            cap.release()
            return thumb_path

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        target_frame = max(0, total_frames // 2)
        cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
        ret, frame = cap.read()
        cap.release()

        if ret and frame is not None:
            cv2.imwrite(str(thumb_path), frame)
        return thumb_path
    finally:
        os.unlink(tmp_path)


def _save_verification_payload(
    user: dict[str, str],
    result,
    camera_image_path: Path,
    database_image_path: Path,
) -> Path:
    PAYLOADS_DIR.mkdir(parents=True, exist_ok=True)
    payload_path = PAYLOADS_DIR / f"verify_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}.json"
    payload = {
        "name": user["full_name"],
        "national_id": user["national_id"],
        "database_image": str(database_image_path),
        "camera_image": str(camera_image_path),
        "match": bool(result.matched),
        "distance": float(result.distance),
        "similarity_score": round(1 - float(result.distance), 6),
        "liveness": result.liveness,
        "confidence": float(result.confidence),
        "verified": bool(result.matched) and result.liveness == "LIVE",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    with open(payload_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload_path


def get_service() -> FaceVerificationService:
    global _service_cache, _service_cache_mtime

    if not DB_DATA_PATH.exists():
        raise FileNotFoundError(
            f"Database file not found: {DB_DATA_PATH}. "
            "Please add a 'database.json' file with identity data."
        )

    current_mtime = os.path.getmtime(DB_DATA_PATH)
    if _service_cache is None or _service_cache_mtime != current_mtime:
        with _service_lock:
            if _service_cache is None or _service_cache_mtime != current_mtime:
                with open(DB_DATA_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    raise ValueError("database.json must contain a list of identities.")

                embedding_service = _get_embedding_service()
                identities: list[Identity] = []
                for index, record in enumerate(data, start=1):
                    identity = _validate_identity_record(record, index)
                    image_name = str(record.get("image_name", "")).strip()
                    if image_name:
                        image_path = BASE_DIR / image_name
                        if not image_path.exists():
                            logger.warning(
                                "Skipping identity '%s' because image file was not found: %s",
                                identity.id,
                                image_path,
                            )
                            continue
                        identity.embedding = embedding_service._get_embedding_from_path(image_path)
                    identities.append(identity)

                if not identities:
                    raise HTTPException(
                        status_code=503,
                        detail="No usable identities are available. Check database.json embeddings or image files.",
                    )

                _service_cache = embedding_service.with_identities(identities)
                _service_cache_mtime = current_mtime

    return _service_cache


# ── Auto Camera Capture ──────────────────────────────────────────────────

def _auto_capture_from_camera(
    timeout_seconds: float = 30.0,
    stable_frames: int = 5,
) -> bytes:
    camera = cv2.VideoCapture(0)
    if not camera.isOpened():
        raise RuntimeError("Camera is not available on the server.")

    embedding_service = _get_embedding_service()
    consecutive_detected = 0
    start_time = time.time()
    captured_frame = None

    try:
        while True:
            elapsed = time.time() - start_time
            if elapsed > timeout_seconds:
                raise RuntimeError("Timeout: no stable face detected within the time limit.")

            ok, frame = camera.read()
            if not ok:
                continue

            # Mirror for natural preview
            frame = cv2.flip(frame, 1)

            # Encode frame to bytes for analysis
            success, encoded = cv2.imencode(".jpg", frame)
            if not success:
                continue
            image_bytes = encoded.tobytes()

            # Analyze face detection
            try:
                metrics = embedding_service.analyze_image_bytes(image_bytes)
                detected = metrics.detected
            except Exception:
                detected = False

            # Status overlay
            if detected:
                consecutive_detected += 1
                status_text = f"Face detected ({consecutive_detected}/{stable_frames})"
                color = (0, 255, 0)
            else:
                consecutive_detected = 0
                status_text = "Searching for face..."
                color = (0, 165, 255)

            cv2.putText(frame, status_text, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2, cv2.LINE_AA)
            remaining = max(0, int(timeout_seconds - elapsed))
            cv2.putText(frame, f"Timeout in {remaining}s", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 1, cv2.LINE_AA)
            cv2.imshow("Server Face Verification", frame)

            if cv2.waitKey(1) & 0xFF == 27:  # ESC to cancel
                raise RuntimeError("Face capture cancelled by operator.")

            if consecutive_detected >= stable_frames:
                captured_frame = frame.copy()
                break
    finally:
        camera.release()
        cv2.destroyAllWindows()

    if captured_frame is None:
        raise RuntimeError("Failed to capture a stable face image.")

    success, encoded = cv2.imencode(".jpg", captured_frame)
    if not success:
        raise RuntimeError("Failed to encode captured frame.")
    return encoded.tobytes()


@app.get("/")
def home() -> FileResponse:
    return FileResponse(
        WEB_DIR / "index.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analyze-frame")
async def analyze_frame(image: UploadFile = File(...)) -> dict[str, float | bool]:
    try:
        image_bytes = await _read_validated_image(image)
        service = get_service()
        metrics = service.analyze_image_bytes(image_bytes)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Frame analysis failed: {exc}") from exc

    return {
        "detected": metrics.detected,
        "center_x_ratio": metrics.center_x_ratio,
        "center_y_ratio": metrics.center_y_ratio,
        "width_ratio": metrics.width_ratio,
        "height_ratio": metrics.height_ratio,
    }


@app.post("/verify-frame")
async def verify_frame(request: Request, image: UploadFile = File(...)) -> dict[str, float | bool | str | None]:
    try:
        image_bytes = await _read_validated_image(image)
        _enforce_request_security(request, image_bytes)
        service = get_service()
        assessment, result = service.assess_and_verify_image_bytes(image_bytes)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Verification failed: {exc}") from exc

    if result is None:
        return {
            "status": "waiting",
            "message": assessment.message,
            "matched": None,
            "distance": None,
            "threshold": service.match_threshold,
        }

    return {
        "status": "frame_checked",
        "message": assessment.message,
        "matched": result.matched,
        "distance": result.distance,
        "threshold": service.match_threshold,
        "liveness": result.liveness,
        "confidence": result.confidence,
        "verified": result.matched and result.liveness == "LIVE",
        "identity_id": result.identity_id,
        "identity_name": result.identity_name
    }


@app.post("/face-verify")
async def face_verify(request: Request, image: UploadFile = File(...)) -> dict[str, float | bool | str | None]:
    try:
        image_bytes = await _read_validated_image(image)
        _enforce_request_security(request, image_bytes)
        service = get_service()
        result = service.verify_image_bytes(image_bytes)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Verification failed: {exc}") from exc

    return _format_verification_response(result)


@app.post("/verify-identity/{identity_id}")
async def verify_identity(identity_id: str, request: Request, image: UploadFile = File(...)) -> dict[str, float | bool | str | None]:
    try:
        identity_id = _validate_identity_id(identity_id)
        image_bytes = await _read_validated_image(image)
        _enforce_request_security(request, image_bytes)
        service = get_service()
        result = service.verify_identity_bytes(identity_id, image_bytes)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Verification failed: {exc}") from exc

    return _format_verification_response(result)


@app.post("/verify-identity")
async def verify_identity_form(
    request: Request,
    identity_id: str = Form(...),
    image: UploadFile = File(...),
) -> dict[str, float | bool | str | None]:
    return await verify_identity(identity_id, request, image)


@app.post("/verify")
async def verify(request: Request, image: UploadFile = File(...)) -> dict[str, float | bool | str | None]:
    return await face_verify(request, image)


# ── UPDATED: Video-based national_id verification ────────────────────────

@app.post("/api/verify-user")
async def verify_user_from_db(
    request: Request,
    national_id: str = Form(...),
    video: UploadFile = File(...),
) -> dict[str, float | bool | str]:
    try:
        clean_national_id = _validate_national_id(national_id)
        video_bytes = await _read_validated_video(video)
        _validate_video_duration(video_bytes)
        _enforce_request_security(request, video_bytes)

        user = _fetch_user_by_national_id(clean_national_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found for this national_id")
        if not user["img"]:
            raise HTTPException(status_code=404, detail="No database image configured for this user")

        db_image_path = _resolve_image_path(user["img"])
        if not db_image_path.exists():
            raise HTTPException(status_code=404, detail=f"Database image file not found: {db_image_path}")

        # Build service with reference identity
        embedding_service = _get_embedding_service()
        reference_embedding = embedding_service._get_embedding_from_path(db_image_path)
        service = embedding_service.with_identities(
            [Identity(id=user["national_id"], name=user["full_name"], embedding=reference_embedding)]
        )

        # Verify video against the specific identity
        result = service.verify_video_bytes(user["national_id"], video_bytes)

        # Save thumbnail from video for audit
        camera_image_path = _extract_thumbnail_from_video_bytes(video_bytes, user["national_id"])
        payload_path = _save_verification_payload(user, result, camera_image_path, db_image_path)
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=500, detail=f"MySQL error: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Verification failed: {exc}") from exc

    verified = bool(result.matched) and result.liveness == "LIVE"
    return {
        "status": "matched" if verified else "not_matched",
        "message": "مطابق" if verified else "غير مطابق",
        "name": user["full_name"],
        "national_id": user["national_id"],
        "database_image": str(db_image_path),
        "camera_image": str(camera_image_path),
        "match": bool(result.matched),
        "distance": float(result.distance),
        "similarity_score": round(1 - float(result.distance), 6),
        "liveness": result.liveness,
        "confidence": float(result.confidence),
        "verified": verified,
        "quality_acceptable": result.quality_acceptable,
        "quality_issues": result.quality_issues or [],
        "payload_json": str(payload_path),
    }


@app.get("/api/integration-spec")
def integration_spec() -> dict[str, object]:
    return {
        "endpoint": "/api/verify-user",
        "method": "POST",
        "content_type": "multipart/form-data",
        "required_fields": [
            {
                "name": "national_id",
                "type": "string",
                "description": "Egyptian national ID or user national_id from database",
                "example": "30210122603309",
            },
            {
                "name": "video",
                "type": "file",
                "description": "3-second video of the person for liveness and face verification",
                "allowed_types": list(VIDEO_CFG.allowed_content_types),
                "max_size_mb": VIDEO_CFG.max_upload_bytes // (1024 * 1024),
            },
        ],
        "optional_fields": [],
        "success_response_fields": [
            "status",
            "message",
            "name",
            "national_id",
            "database_image",
            "camera_image",
            "match",
            "distance",
            "similarity_score",
            "liveness",
            "confidence",
            "verified",
            "quality_acceptable",
            "quality_issues",
            "payload_json",
        ],
        "error_codes": [400, 404, 409, 413, 429, 500],
    }


