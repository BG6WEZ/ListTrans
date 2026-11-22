# -*- coding: utf-8 -*-
"""
Audit, Statistics, Heartbeat, and Server Health Monitoring Module.
Provides:
1. Audit log recording and querying.
2. Real-time online and active user tracking via heartbeats.
3. System aggregate statistics (total logins, usage sessions, processed rows, exported rows, usage duration).
4. Real-time server health metrics (CPU, RAM, Disk) and 90% load alert snapshot capture.
"""

import os
import sys
import time
import json
import sqlite3
import datetime
import threading
from typing import List, Dict, Any, Optional, Tuple

try:
    import psutil
except ImportError:
    psutil = None

from app.auth_manager import get_auth_connection, security_logger

# Thread-safe in-memory state tracking
_state_lock = threading.Lock()
_active_matching_tasks: Dict[str, Dict[str, Any]] = {}
_server_start_time = time.time()
_last_alert_snapshot_time: float = 0.0

def init_audit_tables():
    """Initialize audit_logs, active_heartbeats, and load_alert_snapshots tables."""
    conn = get_auth_connection()
    cursor = conn.cursor()
    
    # 1. Audit logs table (all user operation records)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT NOT NULL,
            name TEXT,
            role TEXT,
            action TEXT NOT NULL,
            details TEXT,
            ip_address TEXT,
            status TEXT DEFAULT 'success',
            duration_ms INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 2. Active heartbeats table (for online & active duration tracking)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS active_heartbeats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            name TEXT,
            role TEXT,
            dept TEXT,
            ip_address TEXT,
            current_page TEXT,
            state TEXT DEFAULT 'idle',
            last_heartbeat DATETIME DEFAULT CURRENT_TIMESTAMP,
            session_start DATETIME DEFAULT CURRENT_TIMESTAMP,
            total_active_seconds INTEGER DEFAULT 0
        )
    """)
    
    # 3. Load alert snapshots table (snapshots when CPU/RAM >= 90% or triggered)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS load_alert_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            trigger_type TEXT NOT NULL,
            cpu_percent REAL NOT NULL,
            memory_percent REAL NOT NULL,
            disk_percent REAL NOT NULL,
            online_users_count INTEGER NOT NULL,
            active_users_count INTEGER NOT NULL,
            processing_rows_count INTEGER NOT NULL,
            active_users_detail TEXT,
            active_tasks_detail TEXT
        )
    """)
    
    # 4. Global system stats accumulator table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_stats_summary (
            stat_key TEXT PRIMARY KEY,
            stat_value INTEGER DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Initialize default stat keys if missing
    cursor.execute("INSERT OR IGNORE INTO system_stats_summary (stat_key, stat_value) VALUES ('total_processed_rows', 0)")
    cursor.execute("INSERT OR IGNORE INTO system_stats_summary (stat_key, stat_value) VALUES ('total_exported_rows', 0)")
    cursor.execute("INSERT OR IGNORE INTO system_stats_summary (stat_key, stat_value) VALUES ('total_usage_duration_seconds', 0)")
    cursor.execute("INSERT OR IGNORE INTO system_stats_summary (stat_key, stat_value) VALUES ('total_usage_sessions', 0)")

    conn.commit()
    conn.close()

def log_audit_event(
    username: str,
    action: str,
    details: str = "",
    name: Optional[str] = None,
    role: Optional[str] = None,
    ip_address: Optional[str] = "127.0.0.1",
    status: str = "success",
    duration_ms: int = 0,
    user_id: Optional[int] = None
):
    """Record an operation audit log entry in audit_logs table."""
    try:
        conn = get_auth_connection()
        cursor = conn.cursor()
        
        # If name or role or user_id is missing, look up from users table
        if not name or not role or not user_id:
            cursor.execute("SELECT id, name, role FROM users WHERE username = ? OR phone = ?", (username, username))
            row = cursor.fetchone()
            if row:
                user_id = user_id or row["id"]
                name = name or row["name"] or username
                role = role or row["role"] or "employee"
            else:
                name = name or username
                role = role or "employee"

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            INSERT INTO audit_logs (
                user_id, username, name, role, action, details, ip_address, status, duration_ms, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, username, name, role, action, details, ip_address, status, duration_ms, now_str
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        security_logger.error(f"Failed to log audit event ({action}): {e}")

def record_heartbeat(
    username: str,
    name: Optional[str] = None,
    role: Optional[str] = None,
    dept: Optional[str] = None,
    ip_address: Optional[str] = "127.0.0.1",
    current_page: str = "workbench",
    state: str = "active"
):
    """
    Update or insert client heartbeat.
    Accumulates active usage duration (20s per active heartbeat).
    """
    try:
        conn = get_auth_connection()
        cursor = conn.cursor()
        now = datetime.datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        
        cursor.execute("SELECT * FROM active_heartbeats WHERE username = ?", (username,))
        row = cursor.fetchone()
        
        if row:
            last_hb = datetime.datetime.strptime(row["last_heartbeat"], "%Y-%m-%d %H:%M:%S")
            diff_sec = (now - last_hb).total_seconds()
            
            # If heartbeat gap is <= 60 seconds, increment total_active_seconds
            add_sec = int(min(60, diff_sec)) if diff_sec > 0 else 20
            new_active_sec = row["total_active_seconds"] + add_sec
            
            cursor.execute("""
                UPDATE active_heartbeats SET
                    name = COALESCE(?, name),
                    role = COALESCE(?, role),
                    dept = COALESCE(?, dept),
                    ip_address = ?,
                    current_page = ?,
                    state = ?,
                    last_heartbeat = ?,
                    total_active_seconds = ?
                WHERE username = ?
            """, (name, role, dept, ip_address, current_page, state, now_str, new_active_sec, username))
            
            # Also update global total_usage_duration_seconds
            cursor.execute("""
                UPDATE system_stats_summary 
                SET stat_value = stat_value + ?, updated_at = ?
                WHERE stat_key = 'total_usage_duration_seconds'
            """, (add_sec, now_str))
        else:
            cursor.execute("""
                INSERT INTO active_heartbeats (
                    username, name, role, dept, ip_address, current_page, state, last_heartbeat, session_start, total_active_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 20)
            """, (username, name or username, role or "employee", dept or "", ip_address, current_page, state, now_str, now_str))
            
            # Increment total usage sessions
            cursor.execute("""
                UPDATE system_stats_summary 
                SET stat_value = stat_value + 1, updated_at = ?
                WHERE stat_key = 'total_usage_sessions'
            """, (now_str,))
            
        conn.commit()
        conn.close()
    except Exception as e:
        security_logger.error(f"Failed to record heartbeat for '{username}': {e}")

def update_processing_task(task_id: str, filename: str, total_rows: int, status: str = "processing", current_step: str = ""):
    """Track in-memory matching/cleaning task."""
    with _state_lock:
        if status in ("completed", "failed"):
            if task_id in _active_matching_tasks:
                del _active_matching_tasks[task_id]
        else:
            _active_matching_tasks[task_id] = {
                "task_id": task_id,
                "filename": filename,
                "total_rows": total_rows,
                "status": status,
                "current_step": current_step,
                "started_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

def increment_stat_counter(stat_key: str, increment_by: int = 1):
    """Atomically increment a global metric counter in system_stats_summary."""
    try:
        conn = get_auth_connection()
        cursor = conn.cursor()
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            UPDATE system_stats_summary 
            SET stat_value = stat_value + ?, updated_at = ?
            WHERE stat_key = ?
        """, (increment_by, now_str, stat_key))
        conn.commit()
        conn.close()
    except Exception as e:
        security_logger.error(f"Failed to increment stat counter '{stat_key}': {e}")

def get_online_and_active_users() -> Dict[str, Any]:
    """
    Query online and active users.
    Online: last_heartbeat within 2 minutes.
    Active in use: last_heartbeat within 2 minutes AND (state != 'idle' or task running).
    """
    conn = get_auth_connection()
    cursor = conn.cursor()
    
    two_minutes_ago = (datetime.datetime.now() - datetime.timedelta(seconds=120)).strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute("""
        SELECT username, name, role, dept, ip_address, current_page, state, last_heartbeat, session_start, total_active_seconds
        FROM active_heartbeats
        WHERE last_heartbeat >= ?
        ORDER BY last_heartbeat DESC
    """, (two_minutes_ago,))
    
    rows = cursor.fetchall()
    conn.close()
    
    online_users = [dict(r) for r in rows]
    
    # Check currently active users
    active_users = [u for u in online_users if u.get("state") in ("active", "matching", "editing", "working")]
    
    with _state_lock:
        processing_rows = sum(t.get("total_rows", 0) for t in _active_matching_tasks.values())
        active_tasks = list(_active_matching_tasks.values())
        
    return {
        "online_count": len(online_users),
        "active_count": len(active_users),
        "processing_rows_count": processing_rows,
        "active_tasks_count": len(active_tasks),
        "online_users": online_users,
        "active_tasks": active_tasks
    }

def get_server_hardware_metrics() -> Dict[str, Any]:
    """Retrieve live CPU, Memory, Disk, and Process health metrics."""
    metrics = {
        "cpu": {
            "percent": 0.0,
            "core_count": os.cpu_count() or 1,
            "logical_count": os.cpu_count() or 1,
            "status": "normal"
        },
        "memory": {
            "total_gb": 0.0,
            "used_gb": 0.0,
            "free_gb": 0.0,
            "percent": 0.0,
            "status": "normal"
        },
        "disk": {
            "total_gb": 0.0,
            "used_gb": 0.0,
            "free_gb": 0.0,
            "percent": 0.0,
            "status": "normal",
            "mount": "C:\\" if os.name == "nt" else "/"
        },
        "process": {
            "pid": os.getpid(),
            "python_version": sys.version.split()[0],
            "uptime_seconds": int(time.time() - _server_start_time),
            "memory_mb": 0.0,
            "threads_count": threading.active_count()
        },
        "metrics_available": False,
        "metrics_source": "unavailable",
        "metrics_error": "",
        "is_alert": False,
        "alert_message": ""
    }
    
    if psutil:
        try:
            # CPU
            cpu_pct = psutil.cpu_percent(interval=0.1)
            metrics["cpu"]["percent"] = round(cpu_pct, 1)
            metrics["cpu"]["logical_count"] = psutil.cpu_count(logical=True) or 1
            metrics["cpu"]["core_count"] = psutil.cpu_count(logical=False) or 1
            if cpu_pct >= 90:
                metrics["cpu"]["status"] = "critical"
            elif cpu_pct >= 75:
                metrics["cpu"]["status"] = "warning"
                
            # Memory
            mem = psutil.virtual_memory()
            metrics["memory"]["total_gb"] = round(mem.total / (1024 ** 3), 2)
            metrics["memory"]["used_gb"] = round(mem.used / (1024 ** 3), 2)
            metrics["memory"]["free_gb"] = round(mem.available / (1024 ** 3), 2)
            metrics["memory"]["percent"] = round(mem.percent, 1)
            if mem.percent >= 90:
                metrics["memory"]["status"] = "critical"
            elif mem.percent >= 75:
                metrics["memory"]["status"] = "warning"
                
            # Disk
            disk_path = os.getcwd()
            disk = psutil.disk_usage(disk_path)
            metrics["disk"]["total_gb"] = round(disk.total / (1024 ** 3), 2)
            metrics["disk"]["used_gb"] = round(disk.used / (1024 ** 3), 2)
            metrics["disk"]["free_gb"] = round(disk.free / (1024 ** 3), 2)
            metrics["disk"]["percent"] = round(disk.percent, 1)
            metrics["disk"]["mount"] = os.path.splitdrive(disk_path)[0] or "/"
            if disk.percent >= 90:
                metrics["disk"]["status"] = "critical"
            elif disk.percent >= 80:
                metrics["disk"]["status"] = "warning"
                
            # Process
            proc = psutil.Process(os.getpid())
            metrics["process"]["memory_mb"] = round(proc.memory_info().rss / (1024 ** 2), 1)
            metrics["process"]["threads_count"] = proc.num_threads()

            has_core_metrics = (
                metrics["memory"]["total_gb"] > 0
                and metrics["disk"]["total_gb"] > 0
                and metrics["process"]["memory_mb"] > 0
            )
            metrics["metrics_source"] = "psutil"
            metrics["metrics_available"] = bool(has_core_metrics)
            if not has_core_metrics:
                metrics["metrics_error"] = "psutil returned incomplete zero hardware metrics"
            
        except Exception as e:
            metrics["metrics_available"] = False
            metrics["metrics_source"] = "unavailable"
            metrics["metrics_error"] = str(e)
            security_logger.error(f"Error querying psutil hardware metrics: {e}")
    else:
        metrics["metrics_source"] = "fallback"
        # Fallback for Linux / generic environments without psutil
        try:
            import shutil
            disk_path = os.getcwd()
            total, used, free = shutil.disk_usage(disk_path)
            metrics["disk"]["total_gb"] = round(total / (1024 ** 3), 2)
            metrics["disk"]["used_gb"] = round(used / (1024 ** 3), 2)
            metrics["disk"]["free_gb"] = round(free / (1024 ** 3), 2)
            metrics["disk"]["percent"] = round((used / total) * 100, 1) if total > 0 else 0.0
            metrics["disk"]["mount"] = os.path.splitdrive(disk_path)[0] or "/"
            if metrics["disk"]["percent"] >= 90:
                metrics["disk"]["status"] = "critical"
            elif metrics["disk"]["percent"] >= 80:
                metrics["disk"]["status"] = "warning"
        except Exception as e:
            security_logger.error(f"Disk fallback error: {e}")

        # Linux /proc/meminfo fallback
        if os.path.exists("/proc/meminfo"):
            try:
                meminfo = {}
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        parts = line.split(":")
                        if len(parts) == 2:
                            key = parts[0].strip()
                            val = parts[1].strip().split()[0]
                            meminfo[key] = int(val)
                total_kb = meminfo.get("MemTotal", 0)
                avail_kb = meminfo.get("MemAvailable", meminfo.get("MemFree", 0) + meminfo.get("Buffers", 0) + meminfo.get("Cached", 0))
                used_kb = max(0, total_kb - avail_kb)
                if total_kb > 0:
                    metrics["memory"]["total_gb"] = round(total_kb / (1024 ** 2), 2)
                    metrics["memory"]["used_gb"] = round(used_kb / (1024 ** 2), 2)
                    metrics["memory"]["free_gb"] = round(avail_kb / (1024 ** 2), 2)
                    metrics["memory"]["percent"] = round((used_kb / total_kb) * 100, 1)
                    if metrics["memory"]["percent"] >= 90:
                        metrics["memory"]["status"] = "critical"
                    elif metrics["memory"]["percent"] >= 75:
                        metrics["memory"]["status"] = "warning"
            except Exception as e:
                security_logger.error(f"Memory fallback error: {e}")

        # Linux /proc/loadavg fallback for CPU
        if os.path.exists("/proc/loadavg"):
            try:
                with open("/proc/loadavg", "r") as f:
                    lavg = float(f.read().split()[0])
                cores = metrics["cpu"]["logical_count"] or 1
                cpu_pct = min(100.0, round((lavg / cores) * 100, 1))
                metrics["cpu"]["percent"] = cpu_pct
                if cpu_pct >= 90:
                    metrics["cpu"]["status"] = "critical"
                elif cpu_pct >= 75:
                    metrics["cpu"]["status"] = "warning"
            except Exception as e:
                security_logger.error(f"CPU loadavg fallback error: {e}")

        # Process fallback
        try:
            import resource
            usage = resource.getrusage(resource.RUSAGE_SELF)
            # maxrss on Linux is in kilobytes
            metrics["process"]["memory_mb"] = round(usage.ru_maxrss / 1024, 1)
        except Exception:
            pass

        has_fallback_metrics = (
            metrics["memory"]["total_gb"] > 0
            and metrics["disk"]["total_gb"] > 0
            and metrics["process"]["memory_mb"] > 0
        )
        metrics["metrics_available"] = bool(has_fallback_metrics)
        if not has_fallback_metrics:
            metrics["metrics_error"] = "psutil unavailable and system fallback metrics incomplete"

    # Check if currently in high-load alert state
    is_cpu_alert = metrics["cpu"]["percent"] >= 90.0
    is_mem_alert = metrics["memory"]["percent"] >= 90.0
    
    if is_cpu_alert or is_mem_alert:
        metrics["is_alert"] = True
        alerts = []
        if is_cpu_alert:
            alerts.append(f"CPU负荷警戒 ({metrics['cpu']['percent']}%)")
        if is_mem_alert:
            alerts.append(f"内存负荷警戒 ({metrics['memory']['percent']}%)")
        metrics["alert_message"] = "；".join(alerts)
        
        # Trigger alert snapshot capture with cooldown (at most 1 every 60 seconds)
        check_and_capture_high_load_snapshot(
            trigger_type=metrics["alert_message"],
            cpu_percent=metrics["cpu"]["percent"],
            memory_percent=metrics["memory"]["percent"],
            disk_percent=metrics["disk"]["percent"]
        )
        
    return metrics

def check_and_capture_high_load_snapshot(
    trigger_type: str,
    cpu_percent: float,
    memory_percent: float,
    disk_percent: float,
    force: bool = False
) -> Optional[int]:
    """
    Capture a detailed snapshot of system state when CPU/Memory >= 90% or forced.
    Records: online users count, rows in matching, active users list, active task list.
    """
    global _last_alert_snapshot_time
    now_ts = time.time()
    
    if not force and (now_ts - _last_alert_snapshot_time < 60.0):
        return None  # Cooldown
        
    _last_alert_snapshot_time = now_ts
    
    online_info = get_online_and_active_users()
    
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    users_json = json.dumps(online_info["online_users"], ensure_ascii=False)
    tasks_json = json.dumps(online_info["active_tasks"], ensure_ascii=False)
    
    try:
        conn = get_auth_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO load_alert_snapshots (
                timestamp, trigger_type, cpu_percent, memory_percent, disk_percent,
                online_users_count, active_users_count, processing_rows_count,
                active_users_detail, active_tasks_detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now_str, trigger_type, cpu_percent, memory_percent, disk_percent,
            online_info["online_count"], online_info["active_count"], online_info["processing_rows_count"],
            users_json, tasks_json
        ))
        snapshot_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        security_logger.warning(
            f"High-Load Alert Snapshot #{snapshot_id} captured: {trigger_type}. "
            f"Online: {online_info['online_count']}, Processing Rows: {online_info['processing_rows_count']}."
        )
        return snapshot_id
    except Exception as e:
        security_logger.error(f"Failed to save load alert snapshot: {e}")
        return None

def get_alert_snapshots(limit: int = 50) -> List[Dict[str, Any]]:
    """Retrieve history of high-load alert snapshots."""
    conn = get_auth_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, timestamp, trigger_type, cpu_percent, memory_percent, disk_percent,
               online_users_count, active_users_count, processing_rows_count,
               active_users_detail, active_tasks_detail
        FROM load_alert_snapshots
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    
    results = []
    for r in rows:
        item = dict(r)
        try:
            item["active_users_detail"] = json.loads(item["active_users_detail"] or "[]")
        except Exception:
            item["active_users_detail"] = []
        try:
            item["active_tasks_detail"] = json.loads(item["active_tasks_detail"] or "[]")
        except Exception:
            item["active_tasks_detail"] = []
        results.append(item)
    return results

def get_aggregated_statistics() -> Dict[str, Any]:
    """
    Calculate and return comprehensive system metrics:
    1. 总登录人次 (Total logins)
    2. 使用人次 (Total usage sessions)
    3. 处理的总清单行数 (Total processed input rows)
    4. 输出的行数 (Total exported output rows)
    5. 系统总使用时长 (Total accumulated usage duration)
    6. 各专业处理分布
    7. 用户活跃排行
    """
    conn = get_auth_connection()
    cursor = conn.cursor()
    
    # 1. Total logins
    cursor.execute("SELECT COUNT(*) FROM login_attempts WHERE success = 1")
    total_logins = cursor.fetchone()[0]
    
    # 2. Total usage sessions
    cursor.execute("SELECT COUNT(*) FROM work_sessions")
    db_sessions = cursor.fetchone()[0]
    cursor.execute("SELECT stat_value FROM system_stats_summary WHERE stat_key = 'total_usage_sessions'")
    row = cursor.fetchone()
    stat_sessions = row[0] if row else 0
    total_usage_sessions = max(db_sessions, stat_sessions)
    
    # 3. Total processed rows
    cursor.execute("SELECT stat_value FROM system_stats_summary WHERE stat_key = 'total_processed_rows'")
    row = cursor.fetchone()
    total_processed_rows = row[0] if row else 0
    
    # 4. Total exported rows
    cursor.execute("SELECT stat_value FROM system_stats_summary WHERE stat_key = 'total_exported_rows'")
    row = cursor.fetchone()
    total_exported_rows = row[0] if row else 0
    
    # 5. Total system usage duration (seconds)
    cursor.execute("SELECT SUM(total_active_seconds) FROM active_heartbeats")
    row = cursor.fetchone()
    sum_active_seconds = row[0] or 0
    cursor.execute("SELECT stat_value FROM system_stats_summary WHERE stat_key = 'total_usage_duration_seconds'")
    row = cursor.fetchone()
    stat_duration = row[0] if row else 0
    total_duration_seconds = max(sum_active_seconds, stat_duration)
    
    # Format duration
    hours = total_duration_seconds // 3600
    minutes = (total_duration_seconds % 3600) // 60
    seconds = total_duration_seconds % 60
    formatted_duration = f"{hours}小时 {minutes}分 {seconds}秒"
    
    # 6. Action breakdown in audit logs
    cursor.execute("""
        SELECT action, COUNT(*) as cnt 
        FROM audit_logs 
        GROUP BY action 
        ORDER BY cnt DESC 
        LIMIT 8
    """)
    action_stats = [{"action": r[0], "count": r[1]} for r in cursor.fetchall()]
    
    # 7. User activity ranking (Top 10 active users)
    cursor.execute("""
        SELECT username, name, role, COUNT(*) as op_count, MAX(created_at) as last_op
        FROM audit_logs
        GROUP BY username
        ORDER BY op_count DESC
        LIMIT 10
    """)
    user_ranking = [dict(r) for r in cursor.fetchall()]
    
    # 8. Professional distribution from work sessions
    cursor.execute("""
        SELECT COALESCE(professional, '暖通') as prof, COUNT(*) as cnt 
        FROM work_sessions 
        GROUP BY prof 
        ORDER BY cnt DESC
    """)
    prof_stats = [{"professional": r[0], "count": r[1]} for r in cursor.fetchall()]
    
    conn.close()
    
    return {
        "total_logins": total_logins,
        "total_usage_sessions": total_usage_sessions,
        "total_processed_rows": total_processed_rows,
        "total_exported_rows": total_exported_rows,
        "total_duration_seconds": total_duration_seconds,
        "formatted_duration": formatted_duration,
        "action_stats": action_stats,
        "user_ranking": user_ranking,
        "professional_stats": prof_stats
    }

def get_audit_logs_paginated(
    username: Optional[str] = None,
    action: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = 1,
    page_size: int = 20
) -> Dict[str, Any]:
    """Retrieve audit logs with multi-dimensional filtering and pagination."""
    conn = get_auth_connection()
    cursor = conn.cursor()
    
    where_clauses = []
    params = []
    
    if username and username.strip():
        where_clauses.append("(username LIKE ? OR name LIKE ?)")
        params.extend([f"%{username.strip()}%", f"%{username.strip()}%"])
        
    if action and action.strip():
        where_clauses.append("action = ?")
        params.append(action.strip())
        
    if status and status.strip():
        where_clauses.append("status = ?")
        params.append(status.strip())
        
    if search and search.strip():
        kw = f"%{search.strip()}%"
        where_clauses.append("(username LIKE ? OR name LIKE ? OR details LIKE ? OR action LIKE ? OR ip_address LIKE ?)")
        params.extend([kw, kw, kw, kw, kw])
        
    if start_date and start_date.strip():
        where_clauses.append("created_at >= ?")
        params.append(f"{start_date.strip()} 00:00:00")
        
    if end_date and end_date.strip():
        where_clauses.append("created_at <= ?")
        params.append(f"{end_date.strip()} 23:59:59")
        
    where_str = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    
    # Count total
    count_sql = f"SELECT COUNT(*) FROM audit_logs{where_str}"
    cursor.execute(count_sql, params)
    total_count = cursor.fetchone()[0]
    
    # Query items
    offset = (page - 1) * page_size
    data_sql = f"""
        SELECT id, user_id, username, name, role, action, details, ip_address, status, duration_ms, created_at
        FROM audit_logs
        {where_str}
        ORDER BY id DESC
        LIMIT ? OFFSET ?
    """
    cursor.execute(data_sql, params + [page_size, offset])
    rows = cursor.fetchall()
    
    # Unique actions for filter dropdown
    cursor.execute("SELECT DISTINCT action FROM audit_logs WHERE action IS NOT NULL ORDER BY action ASC")
    action_types = [r[0] for r in cursor.fetchall()]
    
    conn.close()
    
    return {
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total_count + page_size - 1) // page_size),
        "action_types": action_types,
        "items": [dict(r) for r in rows]
    }
