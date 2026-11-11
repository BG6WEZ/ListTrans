# -*- coding: utf-8 -*-
import os
import sqlite3
import datetime
import logging
from typing import List, Dict, Any, Optional, Tuple
from jose import jwt, JWTError
import bcrypt
from dotenv import load_dotenv

load_dotenv()

# Setup security logger
os.makedirs("logs", exist_ok=True)
security_logger = logging.getLogger("security")
security_logger.setLevel(logging.INFO)
if not security_logger.handlers:
    fh = logging.FileHandler("logs/security.log", encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    fh.setFormatter(formatter)
    security_logger.addHandler(fh)

# Database connection
AUTH_DB_PATH = os.path.join(os.path.dirname(__file__), "auth.db")

# JWT configuration
# Security fix (P1-3): Remove predictable hardcoded default key; load from env or auto-generate a secure random 256-bit key
SECRET_KEY = os.getenv("SECRET_KEY")
APP_ENV = os.getenv("APP_ENV", "production").lower()
if not SECRET_KEY:
    if APP_ENV in ("production", "prod"):
        security_logger.warning("No SECRET_KEY set in production environment. A persistent SECRET_KEY should be set in .env to prevent session invalidation on restart.")
    import secrets
    SECRET_KEY = secrets.token_hex(32)
    security_logger.info("Generated secure session ephemeral key for authentication.")
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

def init_auth_db():
    """Initialize authorization database tables and create initial administrator and personnel accounts."""
    conn = get_auth_connection()
    cursor = conn.cursor()
    
    # Create users table
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
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_login DATETIME
        )
    """)
    
    # Migration: add new columns if they do not exist
    user_cols = [r[1] for r in cursor.execute("PRAGMA table_info(users)").fetchall()]
    new_cols = [
        ("name", "TEXT"),
        ("dept1", "TEXT"),
        ("dept2", "TEXT"),
        ("dept3", "TEXT"),
        ("employee_no", "TEXT"),
        ("position", "TEXT"),
        ("phone", "TEXT"),
        ("need_emp_no_setup", "INTEGER DEFAULT 0")
    ]
    for col_info in new_cols:
        col_name = col_info[0]
        col_type = col_info[1]
        if col_name not in user_cols:
            try:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
            except Exception as e:
                security_logger.error(f"Failed to add column {col_name} to users table: {e}")

    # =========================================================================
    # Phase 2: Schema Migration & Data Backfill for Immutable employee_no
    # =========================================================================
    try:
        # 1. Backfill admin employee_no with fixed SYS_ADMIN
        cursor.execute("""
            UPDATE users SET employee_no = 'SYS_ADMIN'
            WHERE username = 'admin' AND (employee_no IS NULL OR TRIM(employee_no) = '')
        """)

        # 2. Backfill empty employee_no with EMP_xxxx and mark need_emp_no_setup=1 (R4)
        empty_rows = cursor.execute("""
            SELECT id FROM users WHERE employee_no IS NULL OR TRIM(employee_no) = '' ORDER BY id ASC
        """).fetchall()
        for row in empty_rows:
            uid = row["id"]
            emp_val = f"EMP_{uid:04d}"
            cursor.execute("UPDATE users SET employee_no = ?, need_emp_no_setup = 1 WHERE id = ?", (emp_val, uid))

        # 3. Create unique index on employee_no to enforce immutability & uniqueness at DB level
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_employee_no ON users(employee_no)")
        
        # 4. Create unique index on username
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username)")
        
        # 5. Create partial unique index on phone (allowing null/empty without conflict)
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone ON users(phone) WHERE phone IS NOT NULL AND phone != ''")
        conn.commit()
    except Exception as e:
        security_logger.error(f"Failed during employee_no schema migration: {e}")
        raise RuntimeError(f"FATAL: Database employee_no unique schema migration failed: {e}")

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
    conn.commit()
    
    # Check and insert default admin account
    cursor.execute("SELECT id, password_hash FROM users WHERE role = 'admin' LIMIT 1")
    admin_row = cursor.fetchone()
    if admin_row is None:
        admin_user = os.getenv("ADMIN_USERNAME", "admin")
        admin_pass = os.getenv("ADMIN_PASSWORD")
        if not admin_pass:
            if APP_ENV in ("production", "prod"):
                security_logger.error("ADMIN_PASSWORD not set in production. Generating secure random initialization password.")
                import secrets
                admin_pass = secrets.token_urlsafe(16)
                security_logger.warning(f"===========================================================")
                security_logger.warning(f"  INITIAL ADMIN CREATED: username='{admin_user}'")
                security_logger.warning(f"  INITIAL ADMIN ONE-TIME PASSWORD: '{admin_pass}'")
                security_logger.warning(f"===========================================================")
            else:
                admin_pass = "admin"
        hashed_pass = get_password_hash(admin_pass)
        
        try:
            cursor.execute(
                "INSERT INTO users (username, password_hash, name, role) VALUES (?, ?, '系统管理员', 'admin')",
                (admin_user, hashed_pass)
            )
            conn.commit()
            security_logger.info(f"Default admin account '{admin_user}' initialized successfully.")
        except sqlite3.IntegrityError:
            cursor.execute(
                "UPDATE users SET role = 'admin', password_hash = ? WHERE username = ?",
                (hashed_pass, admin_user)
            )
            conn.commit()
            security_logger.info(f"Existing account '{admin_user}' promoted to admin.")
    else:
        # R2 Safety check: warn if admin is still using default weak password
        try:
            if verify_password("admin", admin_row["password_hash"]):
                security_logger.warning("SECURITY ALERT: Admin account is still using default weak password 'admin'. Please change it immediately via profile settings.")
        except Exception:
            pass
            
    conn.close()
    
    # Auto import personnel from doc/人员清单.xls if users table only has 1 account
    try:
        import_users_from_excel()
    except Exception as e:
        security_logger.error(f"Auto importing personnel from Excel failed during init_auth_db: {e}")

def import_users_from_excel(excel_path: Optional[str] = None, default_password: str = "hkjj@12345") -> Dict[str, Any]:
    """Import and synchronize users from personnel Excel file."""
    if not excel_path:
        proj_dir = os.path.dirname(os.path.dirname(__file__))
        excel_path = os.path.join(proj_dir, "doc", "人员清单.xls")
        
    if not os.path.exists(excel_path):
        return {"success": False, "count": 0, "message": f"人员清单文件不存在: {excel_path}"}
        
    import xlrd
    book = xlrd.open_workbook(excel_path)
    sheet = book.sheet_by_index(0)
    
    headers = [str(sheet.cell_value(0, c)).strip() for c in range(sheet.ncols)]
    
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
        
    default_hash = get_password_hash(default_password)
    
    conn = get_auth_connection()
    cursor = conn.cursor()
    
    imported_count = 0
    updated_count = 0
    
    for r in range(1, sheet.nrows):
        def get_val(key):
            if key not in col_map:
                return ""
            val = sheet.cell_value(r, col_map[key])
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
            
        # Role mapping based on position
        if "总监" in position:
            role = "director"
        elif any(kw in position for kw in ["经理", "主管"]):
            role = "manager"
        else:
            role = "employee"
            
        username = phone  # 手机号即为登录账号
        
        # Phase 2: In personnel Excel import, employee_no is the PRIMARY UNIQUE KEY
        # If employee_no is provided, match by employee_no first, then phone
        existing = None
        if employee_no:
            cursor.execute("SELECT id, username FROM users WHERE employee_no = ?", (employee_no,))
            existing = cursor.fetchone()
            
        if not existing and phone:
            cursor.execute("SELECT id, username FROM users WHERE phone = ?", (phone,))
            existing = cursor.fetchone()
        
        if existing:
            # When updating existing user from Excel, NEVER overwrite customized username or password_hash!
            # If employee_no was missing, update it; otherwise preserve existing employee_no
            cursor.execute("""
                UPDATE users SET 
                    name = ?, dept1 = ?, dept2 = ?, dept3 = ?,
                    position = ?, phone = ?, role = ?
                WHERE id = ?
            """, (name, dept1, dept2, dept3, position, phone, role, existing["id"]))
            updated_count += 1
        else:
            # Generate default employee_no if empty in Excel
            emp_to_insert = employee_no
            need_setup = 0
            if not emp_to_insert:
                emp_to_insert = f"EMP_TEMP_{phone}"
                need_setup = 1
            cursor.execute("""
                INSERT INTO users (
                    username, password_hash, name, dept1, dept2, dept3,
                    employee_no, position, phone, role, is_active, need_emp_no_setup
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """, (username, default_hash, name, dept1, dept2, dept3, emp_to_insert, position, phone, role, need_setup))
            imported_count += 1
            
    conn.commit()
    conn.close()
    
    security_logger.info(f"Personnel import completed: {imported_count} created, {updated_count} updated.")
    return {
        "success": True, 
        "imported_count": imported_count, 
        "updated_count": updated_count, 
        "total": imported_count + updated_count
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
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_access_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None

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
    
    # Check failures in last 5 minutes
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
        
    # Check if there is any success after the 5 failures
    consecutive_failures = 0
    first_failure_time = None
    
    for att in attempts:
        if att["success"] == 1:
            break
        consecutive_failures += 1
        if consecutive_failures == 5:
            # Parse attempted_at string to datetime
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
