# -*- coding: utf-8 -*-
import os
import re
import sqlite3
import datetime
import hashlib
import logging
import secrets
from typing import List, Dict, Any, Optional, Tuple
from jose import jwt, JWTError
import bcrypt
from dotenv import load_dotenv

load_dotenv()

# Setup security logger (test isolation: LOG_DIR redirects runtime logs)
LOG_DIR = os.getenv("LOG_DIR", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
security_logger = logging.getLogger("security")
security_logger.setLevel(logging.INFO)
if not security_logger.handlers:
    fh = logging.FileHandler(os.path.join(LOG_DIR, "security.log"), encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    fh.setFormatter(formatter)
    security_logger.addHandler(fh)

# Database connection (test isolation: AUTH_DB_PATH env redirects to an isolated account DB)
AUTH_DB_PATH = os.getenv(
    "AUTH_DB_PATH", os.path.join(os.path.dirname(__file__), "auth.db")
)


def is_testing_env() -> bool:
    return os.getenv("TESTING", "").strip().lower() in ("1", "true", "yes")


_WEAK_PASSWORDS = {
    "",
    "admin",
    "admin123",
    "123456",
    "12345678",
    "123456789",
    "password",
    "qwerty",
    "hkjj@12345",
    "changeme",
    "change_me",
    "change-me",
    "your-password",
    "listtrans",
}

_WEAK_SECRET_MARKERS = (
    "change-this",
    "replace-with",
    "placeholder",
    "your-secret",
    "changeme",
    "default-secret",
    "default_secret",
    "hkjj@",
)


def is_weak_secret(value: Optional[str]) -> bool:
    if not value or not isinstance(value, str):
        return True
    cleaned = value.strip()
    if len(cleaned) < 16:
        return True
    lowered = cleaned.lower()
    if lowered in _WEAK_PASSWORDS:
        return True
    return any(marker in lowered for marker in _WEAK_SECRET_MARKERS)


def is_weak_password(password: Optional[str]) -> bool:
    if not password or not isinstance(password, str):
        return True
    cleaned = password.strip()
    if len(cleaned) < 8:
        return True
    lowered = cleaned.lower()
    if lowered in _WEAK_PASSWORDS:
        return True
    return any(marker in lowered for marker in ("replace-", "placeholder", "your-", "change-this"))


def validate_password_strength(password: Optional[str]) -> Tuple[bool, str]:
    if not password or not isinstance(password, str):
        return False, "密码不能为空。"
    if len(password) < 8:
        return False, "密码长度至少需要 8 个字符。"
    if password.strip() != password or " " in password:
        return False, "密码首尾不能包含空白或空格。"
    if is_weak_password(password):
        return False, "禁止使用默认、常见或占位弱口令。"
    has_letter = bool(re.search(r"[A-Za-z]", password))
    has_digit = bool(re.search(r"[0-9]", password))
    has_symbol = bool(re.search(r"[^A-Za-z0-9]", password))
    if (int(has_letter) + int(has_digit) + int(has_symbol)) < 2:
        return False, "密码至少需要包含字母、数字或特殊字符中的两类。"
    return True, "ok"


def generate_temp_password() -> str:
    """Generate a unique, high-entropy one-time temporary password satisfying strength policy."""
    return f"Lt!{secrets.token_urlsafe(12)}9"


def _issue_unique_temp_password(
    conn: sqlite3.Connection,
    user_id: Optional[int] = None,
    purpose: str = "init",
) -> str:
    """Allocate a one-time high-entropy temp password verified unique against issued_temp_credentials."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS issued_temp_credentials (
            secret_sha256 TEXT PRIMARY KEY,
            user_id INTEGER,
            purpose TEXT,
            issued_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for _ in range(32):
        candidate = generate_temp_password()
        digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
        existing = conn.execute(
            "SELECT 1 FROM issued_temp_credentials WHERE secret_sha256 = ? LIMIT 1",
            (digest,),
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO issued_temp_credentials (secret_sha256, user_id, purpose) VALUES (?, ?, ?)",
                (digest, user_id, purpose),
            )
            return candidate
    raise RuntimeError("Unable to mint a distinct temporary password after retries.")


# JWT configuration (M4-03: fail-closed in production without valid SECRET_KEY)
SECRET_KEY = os.getenv("SECRET_KEY")
APP_ENV = os.getenv("APP_ENV", "production").lower()
if not SECRET_KEY or (not is_testing_env() and APP_ENV in ("production", "prod") and is_weak_secret(SECRET_KEY)):
    if is_testing_env() or APP_ENV in ("development", "dev", "local", "test", "testing"):
        if not SECRET_KEY:
            SECRET_KEY = secrets.token_hex(32)
            security_logger.info("Generated ephemeral non-production session signing key.")
    else:
        raise RuntimeError(
            "FATAL SECURITY ERROR: Production startup requires a strong non-placeholder SECRET_KEY environment variable."
        )
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))

# Password hashing (using native bcrypt)

def get_auth_connection():
    conn = sqlite3.connect(AUTH_DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass
    return conn


def _is_known_weak_default_hash(hashed_password: Optional[str]) -> bool:
    if not hashed_password or not isinstance(hashed_password, str):
        return True
    for weak in ("admin", "hkjj@12345", "123456", "12345678", "password"):
        if verify_password(weak, hashed_password):
            return True
    return False


def init_auth_db():
    """Initialize authorization database tables, run transactional M4 schema migration, and bootstrap admin safely."""
    conn = get_auth_connection()
    cursor = conn.cursor()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing_table = cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        existing_cols = (
            [r[1] for r in cursor.execute("PRAGMA table_info(users)").fetchall()]
            if existing_table
            else []
        )

        # Create users table (with M4 lifecycle columns)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                name TEXT,
                dept1 TEXT,
                dept2 TEXT,
                dept3 TEXT,
                employee_no TEXT,
                position TEXT,
                phone TEXT,
                role TEXT NOT NULL DEFAULT 'employee',
                is_active INTEGER NOT NULL DEFAULT 1,
                need_emp_no_setup INTEGER DEFAULT 0,
                token_version INTEGER NOT NULL DEFAULT 1,
                must_change_password INTEGER NOT NULL DEFAULT 0,
                account_epoch TEXT,
                password_changed_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_login DATETIME
            )
        """)

        user_cols = [r[1] for r in cursor.execute("PRAGMA table_info(users)").fetchall()]
        new_cols = [
            ("name", "TEXT"),
            ("dept1", "TEXT"),
            ("dept2", "TEXT"),
            ("dept3", "TEXT"),
            ("employee_no", "TEXT"),
            ("position", "TEXT"),
            ("phone", "TEXT"),
            ("need_emp_no_setup", "INTEGER DEFAULT 0"),
            ("token_version", "INTEGER NOT NULL DEFAULT 1"),
            ("must_change_password", "INTEGER NOT NULL DEFAULT 0"),
            ("account_epoch", "TEXT"),
            ("password_changed_at", "DATETIME"),
        ]
        for col_name, col_type in new_cols:
            if col_name not in user_cols:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")

        # Backfill M4 account lifecycle fields atomically
        cursor.execute("UPDATE users SET token_version = 1 WHERE token_version IS NULL OR token_version < 1")
        missing_epoch_rows = cursor.execute(
            "SELECT id FROM users WHERE account_epoch IS NULL OR TRIM(account_epoch) = ''"
        ).fetchall()
        for row in missing_epoch_rows:
            cursor.execute(
                "UPDATE users SET account_epoch = ? WHERE id = ?",
                (secrets.token_hex(12), row["id"]),
            )

        # Conservative legacy migration: flag known weak default hashes with must_change_password=1
        # without mutating or batch-resetting existing password hashes.
        if existing_table and "must_change_password" not in existing_cols:
            legacy_rows = cursor.execute("SELECT id, password_hash FROM users").fetchall()
            for row in legacy_rows:
                needs_change = 1 if _is_known_weak_default_hash(row["password_hash"]) else 0
                cursor.execute(
                    "UPDATE users SET must_change_password = ? WHERE id = ?",
                    (needs_change, row["id"]),
                )
        else:
            cursor.execute("UPDATE users SET must_change_password = 0 WHERE must_change_password IS NULL")

        # Phase 2: Schema Migration & Data Backfill for Immutable employee_no
        cursor.execute("""
            UPDATE users SET employee_no = 'SYS_ADMIN'
            WHERE username = 'admin' AND (employee_no IS NULL OR TRIM(employee_no) = '')
        """)
        empty_rows = cursor.execute("""
            SELECT id FROM users WHERE employee_no IS NULL OR TRIM(employee_no) = '' ORDER BY id ASC
        """).fetchall()
        for row in empty_rows:
            uid = row["id"]
            emp_val = f"EMP_{uid:04d}"
            cursor.execute("UPDATE users SET employee_no = ?, need_emp_no_setup = 1 WHERE id = ?", (emp_val, uid))

        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_employee_no ON users(employee_no)")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username)")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone ON users(phone) WHERE phone IS NOT NULL AND phone != ''")

        # Create login_attempts table for security rate limiting
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS login_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                ip_address TEXT,
                attempted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                success INTEGER DEFAULT 0
            )
        """)

        # Create persistent revoked_tokens table (M4-03: survives process restarts)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS revoked_tokens (
                jti TEXT PRIMARY KEY,
                user_id INTEGER,
                token_version INTEGER,
                expires_at INTEGER,
                revoked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                reason TEXT
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_revoked_tokens_expires ON revoked_tokens(expires_at)")

        # Record SHA-256 digests of every server-issued one-time temp password (A-M4-03 uniqueness ledger)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS issued_temp_credentials (
                secret_sha256 TEXT PRIMARY KEY,
                user_id INTEGER,
                purpose TEXT,
                issued_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Check and insert initial admin account without weak defaults or plaintext password logging
        cursor.execute("SELECT id, password_hash FROM users WHERE role = 'admin' LIMIT 1")
        admin_row = cursor.fetchone()
        if admin_row is None:
            admin_user = os.getenv("ADMIN_USERNAME", "admin")
            admin_pass = os.getenv("ADMIN_PASSWORD")
            in_prod = (not is_testing_env()) and (APP_ENV in ("production", "prod"))
            if in_prod:
                if not admin_pass or is_weak_password(admin_pass):
                    raise RuntimeError(
                        "FATAL SECURITY ERROR: ADMIN_PASSWORD must be configured with a strong non-default password in production."
                    )
                must_change_init = 0
            else:
                if not admin_pass:
                    # Synthetic per-process random password in test/dev; never logged or hardcoded as 'admin'
                    admin_pass = generate_temp_password()
                must_change_init = 0

            hashed_pass = get_password_hash(admin_pass)
            epoch = secrets.token_hex(12)
            cursor.execute(
                """
                INSERT INTO users (
                    username, password_hash, name, employee_no, role,
                    is_active, token_version, must_change_password, account_epoch
                ) VALUES (?, ?, '系统管理员', 'SYS_ADMIN', 'admin', 1, 1, ?, ?)
                """,
                (admin_user, hashed_pass, must_change_init, epoch),
            )
            security_logger.info(f"Initial admin account '{admin_user}' initialized securely.")
        else:
            try:
                if verify_password("admin", admin_row["password_hash"]):
                    security_logger.warning("SECURITY ALERT: An admin account uses a legacy weak password hash and requires rotation.")
            except Exception:
                pass

        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
    # M4-03: Never auto-import personnel Excel during init_auth_db().

def import_users_from_excel(
    excel_path: Optional[str] = None,
    default_password: Optional[str] = None,
) -> Dict[str, Any]:
    """Import and synchronize users from personnel Excel file without predictable default passwords."""
    if not excel_path:
        if is_testing_env():
            return {
                "success": False,
                "count": 0,
                "message": "测试模式禁止自动读取默认真实人员清单。",
            }
        proj_dir = os.path.dirname(os.path.dirname(__file__))
        excel_path = os.path.join(proj_dir, "doc", "人员清单.xls")

    if not os.path.exists(excel_path):
        return {"success": False, "count": 0, "message": f"人员清单文件不存在: {excel_path}"}

    if default_password is not None:
        ok_pwd, err_msg = validate_password_strength(default_password)
        if not ok_pwd:
            return {"success": False, "count": 0, "message": "禁止使用默认或弱临时口令批量导入账号。"}

    from app.excel_boundary import ExcelBoundaryError, load_validated_excel_sheets

    try:
        sheets = load_validated_excel_sheets(
            excel_path,
            allowed_exts=frozenset({".xlsx", ".xls"}),
            original_filename=excel_path,
            max_sheets_to_read=1,
            context_label="人员清单导入",
            allow_mock_xlrd=True,
        )
    except ExcelBoundaryError as exc:
        return {"success": False, "count": 0, "message": exc.detail}

    if not sheets or not sheets[0][1]:
        return {"success": False, "count": 0, "message": "人员清单工作表为空"}

    rows = sheets[0][1]
    headers = [str(c).strip() if c is not None else "" for c in rows[0]]

    # Map header names to indices
    col_map = {}
    for idx, h in enumerate(headers):
        if "姓名" in h: col_map["name"] = idx
        elif "1级" in h or "一级" in h: col_map["dept1"] = idx
        elif "2级" in h or "二级" in h: col_map["dept2"] = idx
        elif "3级" in h or "三级" in h: col_map["dept3"] = idx
        elif "工号" in h: col_map["employee_no"] = idx
        elif "职位" in h or "岗位" in h: col_map["position"] = idx
        elif "手机" in h or "电话" in h: col_map["phone"] = idx

    if "phone" not in col_map or "name" not in col_map:
        return {"success": False, "count": 0, "message": "Excel缺少必要列（姓名或手机号）"}

    if len(rows) <= 1:
        return {"success": False, "count": 0, "message": "人员清单缺少数据行"}

    conn = get_auth_connection()
    cursor = conn.cursor()

    imported_count = 0
    updated_count = 0
    role_changed_count = 0
    created_credentials: List[Dict[str, str]] = []

    try:
        conn.execute("BEGIN IMMEDIATE")
        for row_vals in rows[1:]:
            def get_val(key):
                if key not in col_map:
                    return ""
                c_idx = col_map[key]
                if c_idx >= len(row_vals):
                    return ""
                val = row_vals[c_idx]
                if val is None:
                    return ""
                if isinstance(val, float) and val.is_integer():
                    return str(int(val))
                elif isinstance(val, float):
                    return str(val)
                return str(val).strip()

            name = get_val("name")
            phone = get_val("phone")
            dept1 = get_val("dept1")
            dept2 = get_val("dept2")
            dept3 = get_val("dept3")
            employee_no = get_val("employee_no")
            position = get_val("position")

            if not phone or not name:
                continue

            if "总监" in position:
                role = "director"
            elif any(kw in position for kw in ["经理", "主管"]):
                role = "manager"
            else:
                role = "employee"

            username = phone

            existing = None
            if employee_no:
                cursor.execute(
                    "SELECT id, username, role, is_active, token_version FROM users WHERE employee_no = ?",
                    (employee_no,),
                )
                existing = cursor.fetchone()
            if not existing and phone:
                cursor.execute(
                    "SELECT id, username, role, is_active, token_version FROM users WHERE phone = ?",
                    (phone,),
                )
                existing = cursor.fetchone()

            if existing:
                old_role = str(existing["role"] or "")
                role_changed = (old_role != role)
                if role_changed:
                    cursor.execute("""
                        UPDATE users SET
                            name = ?, dept1 = ?, dept2 = ?, dept3 = ?,
                            position = ?, phone = ?, role = ?,
                            token_version = COALESCE(token_version, 1) + 1
                        WHERE id = ?
                    """, (name, dept1, dept2, dept3, position, phone, role, existing["id"]))
                    role_changed_count += 1
                else:
                    cursor.execute("""
                        UPDATE users SET
                            name = ?, dept1 = ?, dept2 = ?, dept3 = ?,
                            position = ?, phone = ?, role = ?
                        WHERE id = ?
                    """, (name, dept1, dept2, dept3, position, phone, role, existing["id"]))
                updated_count += 1
            else:
                emp_to_insert = employee_no
                need_setup = 0
                if not emp_to_insert:
                    emp_to_insert = f"EMP_TEMP_{phone}"
                    need_setup = 1
                # A-M4-03: Ignore any caller-supplied default_password and mint a per-account unique temp secret.
                raw_temp = _issue_unique_temp_password(conn, purpose="excel_import")
                hashed_temp = get_password_hash(raw_temp)
                epoch = secrets.token_hex(12)
                cursor.execute("""
                    INSERT INTO users (
                        username, password_hash, name, dept1, dept2, dept3,
                        employee_no, position, phone, role, is_active, need_emp_no_setup,
                        token_version, must_change_password, account_epoch
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 1, 1, ?)
                """, (username, hashed_temp, name, dept1, dept2, dept3, emp_to_insert, position, phone, role, need_setup, epoch))
                new_uid = cursor.lastrowid
                if new_uid is not None:
                    temp_digest = hashlib.sha256(raw_temp.encode("utf-8")).hexdigest()
                    cursor.execute(
                        "UPDATE issued_temp_credentials SET user_id = ? WHERE secret_sha256 = ?",
                        (int(new_uid), temp_digest),
                    )
                imported_count += 1
                created_credentials.append({
                    "username": username,
                    "name": name,
                    "employee_no": emp_to_insert,
                    "temp_password": raw_temp,
                })
        if (imported_count + updated_count) == 0:
            conn.rollback()
            return {"success": False, "count": 0, "message": "人员清单中未找到包含有效姓名与手机号的数据行"}
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    security_logger.info(
        f"Personnel import completed: {imported_count} created, {updated_count} updated, {role_changed_count} role_changed."
    )
    return {
        "success": True,
        "imported_count": imported_count,
        "updated_count": updated_count,
        "role_changed_count": role_changed_count,
        "default_password_ignored": bool(default_password is not None),
        "total": imported_count + updated_count,
        "created_credentials": created_credentials,
    }


# Password hashing and verification (direct bcrypt)
def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')


# JWT helpers
def create_access_token(data: dict, expires_delta: Optional[datetime.timedelta] = None) -> str:
    to_encode = data.copy()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    if expires_delta:
        expire = now_utc + expires_delta
    else:
        expire = now_utc + datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.setdefault("jti", secrets.token_hex(16))
    to_encode.setdefault("iat", now_utc)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def issue_token_for_user(user: Dict[str, Any], expires_delta: Optional[datetime.timedelta] = None) -> str:
    """Issue a versioned, revocable access token bound to DB uid, token_version, and account_epoch."""
    if not user or not user.get("id") or not user.get("username"):
        raise ValueError("Cannot issue token without persistent user id and username")
    epoch = str(user.get("account_epoch") or "").strip()
    if not epoch:
        raise ValueError("Cannot issue token without account_epoch")
    tv = int(user.get("token_version") or 1)
    jti = secrets.token_hex(16)
    payload = {
        "sub": user["username"],
        "uid": int(user["id"]),
        "role": user.get("role", "employee"),
        "tv": tv,
        "ep": epoch,
        "jti": jti,
    }
    encoded_jwt = create_access_token(payload, expires_delta=expires_delta)
    if isinstance(user, dict):
        user["_auth_token"] = encoded_jwt
        user["_token_jti"] = jti
        user["_token_tv"] = tv
        user["_token_ep"] = epoch
    return encoded_jwt


def verify_access_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None


def revoke_token_jti(
    jti: str,
    user_id: Optional[int] = None,
    token_version: Optional[int] = None,
    expires_at: Optional[int] = None,
    reason: str = "logout",
) -> bool:
    if not jti:
        return False
    conn = get_auth_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT OR IGNORE INTO revoked_tokens (jti, user_id, token_version, expires_at, reason)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(jti), user_id, token_version, expires_at, reason),
        )
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def is_token_revoked(jti: str) -> bool:
    if not jti:
        return True
    conn = get_auth_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM revoked_tokens WHERE jti = ? LIMIT 1", (str(jti),)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def verify_token_session(token: str) -> Tuple[Optional[Dict[str, Any]], int, str]:
    """Fail-closed server-side validation of JWT claims, token_version, account_epoch, active status, and revocation."""
    payload = verify_access_token(token)
    if not payload:
        return None, 401, "登录状态已过期或无效，请重新登录。"

    sub = payload.get("sub")
    uid = payload.get("uid")
    tv = payload.get("tv")
    jti = payload.get("jti")
    ep = payload.get("ep")

    if not sub or uid is None or tv is None or not jti or not ep:
        return None, 401, "Token 缺少必需的安全会话版本信息，请重新登录。"

    try:
        uid_int = int(uid)
        tv_int = int(tv)
    except (TypeError, ValueError):
        return None, 401, "Token 格式无效，请重新登录。"

    conn = None
    try:
        conn = get_auth_connection()
        revoked = conn.execute(
            "SELECT 1 FROM revoked_tokens WHERE jti = ? LIMIT 1", (str(jti),)
        ).fetchone()
        if revoked is not None:
            return None, 401, "登录会话已退出注销，请重新登录。"

        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid_int,)).fetchone()
        if not row:
            return None, 401, "该账号已不存在或已被移除。"

        if row["username"] != sub:
            return None, 401, "登录凭据主体不匹配，请重新登录。"
        if str(row["account_epoch"] or "") != str(ep):
            return None, 401, "账号生命周期已重置，请重新登录。"
        if int(row["token_version"] or 1) != tv_int:
            return None, 401, "登录凭据版本已失效（密码或权限已变更），请重新登录。"
        if not row["is_active"]:
            return None, 403, "该账号已被停用，请联系管理员。"

        user_dict = dict(row)
        user_dict["_auth_token"] = token
        user_dict["_token_jti"] = str(jti)
        user_dict["_token_exp"] = payload.get("exp")
        user_dict["_token_tv"] = tv_int
        return user_dict, 200, "ok"
    except sqlite3.Error as exc:
        security_logger.error(f"Auth DB failure during token verification (fail-closed): {type(exc).__name__}")
        return None, 503, "认证存储当前不可用，系统已安全拒绝访问。"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def resolve_verified_user(user_info: Any) -> Optional[Dict[str, Any]]:
    """Verify an actor carries a valid unrevoked session JWT and matches DB identity/role."""
    if isinstance(user_info, str):
        token = user_info.strip()
        claim_dict: Optional[Dict[str, Any]] = None
    elif isinstance(user_info, dict):
        raw_tok = user_info.get("_auth_token") or user_info.get("access_token") or user_info.get("token")
        token = raw_tok.strip() if isinstance(raw_tok, str) else ""
        claim_dict = user_info
    else:
        return None

    if not token:
        return None

    verified_user, code, _ = verify_token_session(token)
    if not verified_user or code != 200:
        return None

    if claim_dict is not None:
        claim_id = claim_dict.get("id") if claim_dict.get("id") is not None else claim_dict.get("uid")
        if claim_id is not None:
            try:
                if int(claim_id) != int(verified_user["id"]):
                    return None
            except (TypeError, ValueError):
                return None

        claim_username = claim_dict.get("username") or claim_dict.get("sub")
        if claim_username is not None and str(claim_username) != str(verified_user["username"]):
            return None

        claim_role = claim_dict.get("role")
        if claim_role is not None and str(claim_role) != str(verified_user["role"]):
            return None

    return verified_user


def authenticate_user_and_issue_token(
    username_input: str,
    plain_password: str,
    expires_delta: Optional[datetime.timedelta] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str], str]:
    """Authenticate credentials and mint a token bound to the verified pre-check version snapshot.

    Prevents concurrent password changes from letting an old password receive a new token_version JWT.
    """
    user = get_user_by_username(username_input)
    if not user:
        return None, None, "invalid_credentials"
    if not user.get("is_active"):
        return user, None, "inactive"

    snap_id = int(user["id"])
    snap_hash = str(user["password_hash"])
    snap_tv = int(user.get("token_version") or 1)
    snap_ep = str(user.get("account_epoch") or "")

    if not verify_password(plain_password, snap_hash):
        return user, None, "invalid_credentials"

    # Re-verify snapshot under SQLite lock so concurrent credential change rejects stale bcrypt proof
    conn = get_auth_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        latest = conn.execute("SELECT * FROM users WHERE id = ?", (snap_id,)).fetchone()
        if not latest or not latest["is_active"]:
            conn.rollback()
            return user, None, "inactive"
        if (
            str(latest["password_hash"]) != snap_hash
            or int(latest["token_version"] or 1) != snap_tv
            or str(latest["account_epoch"] or "") != snap_ep
        ):
            conn.rollback()
            return dict(latest), None, "credential_changed_concurrently"
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (now_str, snap_id))
        conn.commit()
        fresh_user = dict(latest)
        fresh_user["last_login"] = now_str
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    token = issue_token_for_user(fresh_user, expires_delta=expires_delta)
    return fresh_user, token, "ok"


def change_user_credentials_atomic(
    user_id: int,
    old_password: Optional[str],
    new_username: Optional[str] = None,
    new_password: Optional[str] = None,
    current_jti: Optional[str] = None,
    current_exp: Optional[int] = None,
) -> Dict[str, Any]:
    """Atomically update username/password, clear must_change_password, bump token_version, and revoke current jti."""
    clean_username = new_username.strip() if new_username is not None else None
    conn = get_auth_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        if not row:
            conn.rollback()
            return {"success": False, "status_code": 404, "error": "账号不存在。"}
        if not row["is_active"]:
            conn.rollback()
            return {"success": False, "status_code": 403, "error": "该账号已被停用。"}

        updates = []
        params: List[Any] = []
        bump_version = False
        pwd_changed = False

        if clean_username and clean_username != row["username"]:
            if not old_password or not verify_password(old_password, row["password_hash"]):
                conn.rollback()
                return {"success": False, "status_code": 400, "error": "原密码验证失败，请核对后重试。"}
            dup = conn.execute(
                "SELECT id FROM users WHERE username = ? AND id != ?",
                (clean_username, int(user_id)),
            ).fetchone()
            if dup:
                conn.rollback()
                return {"success": False, "status_code": 400, "error": f"用户名 '{clean_username}' 已被其他账号占用，请更换。"}
            updates.append("username = ?")
            params.append(clean_username)
            bump_version = True

        if new_password is not None and new_password != "":
            if not old_password:
                conn.rollback()
                return {"success": False, "status_code": 400, "error": "修改密码时必须提供正确的原密码。"}
            if not verify_password(old_password, row["password_hash"]):
                conn.rollback()
                return {"success": False, "status_code": 400, "error": "原密码验证失败，请核对后重试。"}
            if new_password == old_password:
                conn.rollback()
                return {"success": False, "status_code": 400, "error": "新密码不能与原/临时密码相同。"}
            ok_pwd, err_msg = validate_password_strength(new_password)
            if not ok_pwd:
                conn.rollback()
                return {"success": False, "status_code": 400, "error": err_msg}
            updates.append("password_hash = ?")
            params.append(get_password_hash(new_password))
            updates.append("must_change_password = 0")
            updates.append("password_changed_at = CURRENT_TIMESTAMP")
            bump_version = True
            pwd_changed = True

        if updates:
            if bump_version:
                updates.append("token_version = COALESCE(token_version, 1) + 1")
            params.append(int(user_id))
            conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)

        if bump_version and current_jti:
            conn.execute(
                """
                INSERT OR IGNORE INTO revoked_tokens (jti, user_id, token_version, expires_at, reason)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(current_jti), int(user_id), int(row["token_version"] or 1), current_exp, "credential_change"),
            )

        updated = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        conn.commit()
        return {
            "success": True,
            "password_changed": pwd_changed,
            "username_changed": bool(clean_username and clean_username != row["username"]),
            "token_version_bumped": bump_version,
            "user": dict(updated) if updated else None,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def admin_create_user_atomic(
    username: str,
    role: str = "employee",
    password: Optional[str] = None,
    name: str = "",
    dept1: str = "",
    dept2: str = "",
    dept3: str = "",
    employee_no: str = "",
    position: str = "",
    phone: str = "",
) -> Dict[str, Any]:
    """Create a user with server-guaranteed unique one-time temp password and must_change_password=1."""
    clean_username = username.strip()
    if not clean_username:
        return {"success": False, "status_code": 400, "error": "用户名不能为空。"}

    caller_supplied_password = password is not None and str(password) != ""
    if caller_supplied_password:
        ok_pwd, err_msg = validate_password_strength(password)
        if not ok_pwd:
            return {"success": False, "status_code": 400, "error": err_msg}

    clean_emp = employee_no.strip() if employee_no else ""
    clean_phone = phone.strip() if phone else ""
    epoch = secrets.token_hex(12)

    conn = get_auth_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT id FROM users WHERE username = ?", (clean_username,)).fetchone():
            conn.rollback()
            return {"success": False, "status_code": 400, "error": f"用户名 '{clean_username}' 已存在。"}
        if clean_emp and conn.execute("SELECT id FROM users WHERE employee_no = ?", (clean_emp,)).fetchone():
            conn.rollback()
            return {"success": False, "status_code": 400, "error": f"工号 '{clean_emp}' 已存在，请核对后输入。"}
        if clean_phone and conn.execute("SELECT id FROM users WHERE phone = ?", (clean_phone,)).fetchone():
            conn.rollback()
            return {"success": False, "status_code": 400, "error": f"手机号 '{clean_phone}' 已被其他账号绑定。"}

        # A-M4-03: Always mint a distinct server-side temporary password per account.
        raw_password = _issue_unique_temp_password(conn, purpose="admin_create")
        pwd_hash = get_password_hash(raw_password)
        final_emp = clean_emp if clean_emp else f"EMP_MAN_{clean_username}"
        conn.execute(
            """
            INSERT INTO users (
                username, password_hash, name, dept1, dept2, dept3,
                employee_no, position, phone, role, is_active,
                token_version, must_change_password, account_epoch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 1, ?)
            """,
            (
                clean_username,
                pwd_hash,
                name or clean_username,
                dept1,
                dept2,
                dept3,
                final_emp,
                position,
                clean_phone,
                role,
                epoch,
            ),
        )
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        temp_digest = hashlib.sha256(raw_password.encode("utf-8")).hexdigest()
        conn.execute(
            "UPDATE issued_temp_credentials SET user_id = ? WHERE secret_sha256 = ?",
            (int(new_id), temp_digest),
        )
        conn.commit()
        return {
            "success": True,
            "id": new_id,
            "username": clean_username,
            "role": role,
            "must_change_password": 1,
            "temp_password": raw_password,
            "server_generated_temp_password": True,
            "caller_password_ignored": caller_supplied_password,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def admin_update_user_atomic(user_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Update user profile/role/is_active atomically, bumping token_version when role or is_active changes."""
    conn = get_auth_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        if not existing:
            conn.rollback()
            return {"success": False, "status_code": 404, "error": "未找到该用户。"}

        fields = []
        params: List[Any] = []
        role_changed = False
        active_changed = False

        for k in ("name", "dept1", "dept2", "dept3", "position", "phone", "employee_no"):
            if k in updates and updates[k] is not None:
                val = str(updates[k]).strip() if isinstance(updates[k], str) else updates[k]
                if k == "employee_no":
                    if not val:
                        conn.rollback()
                        return {"success": False, "status_code": 400, "error": "工号不能为空。"}
                    if conn.execute(
                        "SELECT id FROM users WHERE employee_no = ? AND id != ?", (val, int(user_id))
                    ).fetchone():
                        conn.rollback()
                        return {"success": False, "status_code": 400, "error": f"工号 '{val}' 已被其他账号占用，必须保持全员唯一。"}
                    fields.append("need_emp_no_setup = 0")
                if k == "phone" and val:
                    if conn.execute(
                        "SELECT id FROM users WHERE phone = ? AND id != ?", (val, int(user_id))
                    ).fetchone():
                        conn.rollback()
                        return {"success": False, "status_code": 400, "error": f"手机号 '{val}' 已被其他账号占用。"}
                fields.append(f"{k} = ?")
                params.append(val)

        if "role" in updates and updates["role"] is not None:
            new_role = str(updates["role"])
            if new_role != existing["role"]:
                role_changed = True
            fields.append("role = ?")
            params.append(new_role)

        if "is_active" in updates and updates["is_active"] is not None:
            new_active = 1 if updates["is_active"] else 0
            if new_active != int(existing["is_active"] or 0):
                active_changed = True
            fields.append("is_active = ?")
            params.append(new_active)

        if not fields:
            conn.rollback()
            return {"success": False, "status_code": 400, "error": "未提供需要更新的字段。"}

        if role_changed or active_changed:
            fields.append("token_version = COALESCE(token_version, 1) + 1")

        params.append(int(user_id))
        conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", params)
        updated = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        conn.commit()
        return {
            "success": True,
            "role_changed": role_changed,
            "active_changed": active_changed,
            "token_version_bumped": bool(role_changed or active_changed),
            "user": dict(updated) if updated else None,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def admin_reset_password_atomic(user_id: int, new_password: Optional[str] = None) -> Dict[str, Any]:
    """Reset a user's password to a server-guaranteed unique temp secret, force must_change_password=1, and bump token_version."""
    caller_supplied_password = new_password is not None and str(new_password) != ""
    if caller_supplied_password:
        ok_pwd, err_msg = validate_password_strength(new_password)
        if not ok_pwd:
            return {"success": False, "status_code": 400, "error": err_msg}

    conn = get_auth_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT id, username, name, role FROM users WHERE id = ?", (int(user_id),)).fetchone()
        if not row:
            conn.rollback()
            return {"success": False, "status_code": 404, "error": "用户不存在。"}
        # A-M4-03: Always mint a distinct server-side temporary password on reset.
        raw_password = _issue_unique_temp_password(conn, user_id=int(user_id), purpose="admin_reset")
        pwd_hash = get_password_hash(raw_password)
        conn.execute(
            """
            UPDATE users
            SET password_hash = ?,
                must_change_password = 1,
                token_version = COALESCE(token_version, 1) + 1
            WHERE id = ?
            """,
            (pwd_hash, int(user_id)),
        )
        conn.commit()
        return {
            "success": True,
            "user_id": int(row["id"]),
            "username": row["username"],
            "temp_password": raw_password,
            "must_change_password": 1,
            "server_generated_temp_password": True,
            "caller_password_ignored": caller_supplied_password,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


# User functions
def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """Retrieve user by username, employee_no, or phone (multi-credential lookup)."""
    conn = get_auth_connection()
    cursor = conn.cursor()
    u = username.strip()
    # 1. Match by exact username
    cursor.execute("SELECT * FROM users WHERE username = ?", (u,))
    row = cursor.fetchone()
    if not row:
        # 2. Match by exact employee_no
        cursor.execute("SELECT * FROM users WHERE employee_no = ?", (u,))
        row = cursor.fetchone()
    if not row:
        # 3. Match by exact phone
        cursor.execute("SELECT * FROM users WHERE phone = ?", (u,))
        row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_auth_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def update_user_last_login(user_id: int):
    conn = get_auth_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET last_login = ? WHERE id = ?",
        (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user_id)
    )
    conn.commit()
    conn.close()


# Security Rate Limiting (Account locking after consecutive failures)
def is_user_locked_out(username: str, ip: str) -> Tuple[bool, Optional[int]]:
    """
    Check if account is locked out.
    Rule: 5 failures in 5 minutes locks the account/IP for 15 minutes.
    Returns: (is_locked, remaining_lockout_seconds)
    """
    conn = get_auth_connection()
    cursor = conn.cursor()

    five_minutes_ago = (datetime.datetime.now() - datetime.timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        SELECT attempted_at, success FROM login_attempts
        WHERE (username = ? OR ip_address = ?)
          AND attempted_at >= ?
        ORDER BY attempted_at DESC
    """, (username, ip, five_minutes_ago))

    attempts = cursor.fetchall()
    conn.close()

    if len(attempts) < 5:
        return False, None

    consecutive_failures = 0
    first_failure_time = None

    for att in attempts:
        if att["success"] == 1:
            break
        consecutive_failures += 1
        if consecutive_failures == 5:
            dt = datetime.datetime.strptime(att["attempted_at"], "%Y-%m-%d %H:%M:%S")
            first_failure_time = dt

    if consecutive_failures >= 5 and first_failure_time:
        lockout_expiry = first_failure_time + datetime.timedelta(minutes=15)
        now = datetime.datetime.now()
        if now < lockout_expiry:
            remaining = int((lockout_expiry - now).total_seconds())
            return True, remaining

    return False, None


def log_login_attempt(username: str, ip: str, success: bool):
    conn = get_auth_connection()
    cursor = conn.cursor()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        "INSERT INTO login_attempts (username, ip_address, attempted_at, success) VALUES (?, ?, ?, ?)",
        (username, ip, now_str, 1 if success else 0)
    )
    conn.commit()
    conn.close()
