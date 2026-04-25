from pathlib import Path
import time
import cv2
import json
from datetime import datetime
import mysql.connector

from face_service import FaceVerificationService, Identity


BASE_DIR = Path(__file__).resolve().parent
CAPTURES_DIR = BASE_DIR / "captured_faces"
CAPTURE_PAYLOAD_PATH = BASE_DIR / "capture_payload.json"
MYSQL_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "",
    "database": "",
}
IMAGE_SEARCH_DIRS = (
    BASE_DIR,
    BASE_DIR / "voter_faces",
    BASE_DIR / "captured_faces",
)


def mysql_server_config() -> dict[str, str]:
    return {
        "host": MYSQL_CONFIG["host"],
        "user": MYSQL_CONFIG["user"],
        "password": MYSQL_CONFIG["password"],
    }


def database_has_required_users_table(database_name: str) -> bool:
    conn = mysql.connector.connect(**mysql_server_config(), database=database_name)
    cursor = conn.cursor()
    try:
        cursor.execute("SHOW TABLES LIKE 'users'")
        if cursor.fetchone() is None:
            return False

        cursor.execute(f"DESCRIBE `{database_name}`.`users`")
        columns = {row[0] for row in cursor.fetchall()}
        return {"id", "full_name", "national_id", "img"}.issubset(columns)
    finally:
        cursor.close()
        conn.close()


def resolve_users_database() -> str:
    preferred_names = []
    if MYSQL_CONFIG["database"].strip():
        preferred_names.append(MYSQL_CONFIG["database"].strip())
    preferred_names.extend(["final", "mysql"])

    conn = mysql.connector.connect(**mysql_server_config())
    cursor = conn.cursor()
    try:
        cursor.execute("SHOW DATABASES")
        available = [row[0] for row in cursor.fetchall()]
    finally:
        cursor.close()
        conn.close()

    checked: list[str] = []
    best_database = None
    best_score = -1
    for database_name in preferred_names + available:
        if database_name in checked:
            continue
        checked.append(database_name)
        try:
            if database_has_required_users_table(database_name):
                probe_conn = mysql.connector.connect(**mysql_server_config(), database=database_name)
                probe_cursor = probe_conn.cursor()
                try:
                    probe_cursor.execute(
                        """
                        SELECT
                            SUM(CASE WHEN national_id IS NOT NULL AND national_id <> '' THEN 1 ELSE 0 END) AS national_count,
                            SUM(CASE WHEN img IS NOT NULL AND img <> '' THEN 1 ELSE 0 END) AS image_count
                        FROM users
                        """
                    )
                    national_count, image_count = probe_cursor.fetchone()
                    score = int(national_count or 0) * 10 + int(image_count or 0)
                finally:
                    probe_cursor.close()
                    probe_conn.close()

                if score > best_score:
                    best_score = score
                    best_database = database_name
        except mysql.connector.Error:
            continue

    if best_database:
        return best_database

    raise mysql.connector.Error("Could not find a MySQL database that contains users(id, full_name, national_id, img)")


def fetch_users_from_db() -> list[dict[str, str]]:
    conn = mysql.connector.connect(**mysql_server_config(), database=resolve_users_database())
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT id, full_name, national_id, img
            FROM users
            WHERE full_name IS NOT NULL
              AND national_id IS NOT NULL
            ORDER BY id ASC
            """
        )
        rows = cursor.fetchall()
        users: list[dict[str, str]] = []
        for row in rows:
            users.append(
                {
                    "id": str(row["id"]),
                    "name": str(row["full_name"]).strip(),
                    "national_id": str(row["national_id"]).strip(),
                    "image_path": str(row["img"] or "").strip(),
                }
            )
        return users
    finally:
        cursor.close()
        conn.close()


def resolve_image_path(raw_path: str) -> Path:
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


def build_identities_from_users(users: list[dict[str, str]]) -> list[Identity]:
    bootstrap_service = FaceVerificationService([])
    identities: list[Identity] = []
    for user in users:
        image_path_raw = user.get("image_path", "")
        if not image_path_raw:
            continue
        image_path = resolve_image_path(image_path_raw)
        if not image_path.exists():
            print(f"Skipped {user['name']}: image not found at {image_path}")
            continue
        try:
            embedding = bootstrap_service._get_embedding_from_path(image_path)
            identities.append(
                Identity(
                    id=user["national_id"],
                    name=user["name"],
                    embedding=embedding,
                )
            )
        except ValueError as exc:
            print(f"Skipped {user['name']}: {exc}")
    return identities


def save_capture_payload(
    matched_user: dict[str, str] | None,
    captured_image_path: Path,
    result,
) -> None:
    payload = {
        "name": matched_user["name"] if matched_user else "",
        "national_id": matched_user["national_id"] if matched_user else "",
        "database_image": matched_user["image_path"] if matched_user else "",
        "camera_image": str(captured_image_path),
        "verified": result.matched if result else False,
        "distance": result.distance if result else None,
        "confidence": result.confidence if result else 0.0,
        "liveness": result.liveness if result else "UNKNOWN",
        "quality_acceptable": result.quality_acceptable if result else False,
        "quality_issues": result.quality_issues if result else [],
        "captured_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(CAPTURE_PAYLOAD_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"Saved JSON payload to {CAPTURE_PAYLOAD_PATH}")


def main() -> None:
    try:
        users = fetch_users_from_db()
    except mysql.connector.Error as exc:
        print(f"MySQL connection error: {exc}")
        return

    if not users:
        print("No users found in database table `users`.")
        return

    default_user = users[0]
    identities = build_identities_from_users(users)
    service = FaceVerificationService(identities)
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    camera = cv2.VideoCapture(0)

    if not camera.isOpened():
        print("Camera is not available.")
        return

    print("Press SPACE to capture a face image, or Q to quit.")

    verification_text = ""
    verification_timestamp: float | None = None

    try:
        while True:
            ok, frame = camera.read()
            if not ok:
                print("Failed to read a frame from the camera.")
                return

            if verification_text:
                cv2.putText(
                    frame,
                    verification_text,
                    (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1.0,
                    (0, 255, 0) if verification_text.startswith("Verified") else (0, 0, 255),
                    2,
                    cv2.LINE_AA,
                )

            cv2.imshow("Face Verification", frame)
            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                break

            if key == 32 and verification_timestamp is None:
                capture_file = CAPTURES_DIR / f"capture_{int(time.time())}.jpg"
                cv2.imwrite(str(capture_file), frame)

                matched_user: dict[str, str] | None = default_user
                verified = False
                distance: float | None = None

                try:
                    result = service.verify_image(frame)
                    if result.matched:
                        matched_user = next(
                            (u for u in users if u["national_id"] == (result.identity_id or "")),
                            default_user,
                        )
                        verification_text = f"Verified. Confidence: {result.confidence:.2f} | Dist: {result.distance:.4f}"
                    else:
                        verification_text = f"Not verified. Confidence: {result.confidence:.2f} | Dist: {result.distance:.4f}"
                    print(verification_text)
                except ValueError as exc:
                    verification_text = str(exc)
                    result = None
                    print(verification_text)

                save_capture_payload(matched_user, capture_file, result)
                verification_timestamp = time.time()

            if verification_timestamp is not None:
                elapsed = time.time() - verification_timestamp
                if elapsed >= 2.0:
                    break
    finally:
        camera.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
