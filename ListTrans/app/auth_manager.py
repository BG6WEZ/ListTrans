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
SECRET_KEY = os.getenv("SECRET_KEY", "941a8a25c1103f6f3a388f7c9e0d16eb222a1b32df511ef5e69dfad1f30a597a")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))

# Password hashing (using native bcrypt)

def get_auth_connection():
    conn = sqlite3.connect(AUTH_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_auth_db():
    """Initialize authorization database tables and create initial administrator."""
    conn = get_auth_connection()
    cursor = conn.cursor()
    
    # Create users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'employee',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_login DATETIME
        )
    """)
    
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
    cursor.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1")
    admin_exists = cursor.fetchone() is not None
    if not admin_exists:
        admin_user = os.getenv("ADMIN_USERNAME", "admin")
        admin_pass = os.getenv("ADMIN_PASSWORD", "AdminPassword@2026")
        hashed_pass = get_password_hash(admin_pass)
        
        try:
            cursor.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
                (admin_user, hashed_pass)
            )
            conn.commit()
            security_logger.info(f"Default admin account '{admin_user}' initialized successfully.")
        except sqlite3.IntegrityError:
            # If the user 'admin' exists but doesn't have admin role, promote them
            cursor.execute(
                "UPDATE users SET role = 'admin', password_hash = ? WHERE username = ?",
                (hashed_pass, admin_user)
            )
            conn.commit()
            security_logger.info(f"Existing account '{admin_user}' promoted to admin.")
            
    conn.close()

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
    if expires_delta:
        expire = datetime.datetime.utcnow() + expires_delta
    else:
        expire = datetime.datetime.utcnow() + datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
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
    conn = get_auth_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username.strip(),))
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
