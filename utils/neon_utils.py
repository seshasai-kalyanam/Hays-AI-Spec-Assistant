"""
Neon Database Utilities for CRUD Operations

This module provides utility functions for interacting with a Neon PostgreSQL database
from Streamlit applications. Connections are reused via a module-level connection pool
when available (SimpleConnectionPool); otherwise a direct connection is used per call.

The module expects database credentials to be stored in Streamlit secrets under
the 'neon_db' key with the following structure:
    [neon_db]
    user = "your_username"
    password = "your_password"
    host = "your_host.neon.tech"
    database = "your_database_name"
    port = 5432  # Optional, defaults to 5432

Example usage:
    import streamlit as st
    from utils.neon_utils import create_log_entry, read_log_by_id
    
    # Create a new log entry
    log_entry = create_log_entry(
        user_email="user@example.com",
        user_prompt="What is AI?",
        llm_response_snippet="AI is artificial intelligence...",
        feedback=1
    )
    
    # Read a log by ID
    log = read_log_by_id(log_entry["id"])
"""

import streamlit as st
import psycopg2
from psycopg2 import OperationalError, InterfaceError, pool
from psycopg2.extras import Json, RealDictCursor
from typing import Optional, Dict, Any

_connection_pool: Optional[pool.SimpleConnectionPool] = None
_CONNECTION_ERRORS = (OperationalError, InterfaceError)
_POOL_ACQUIRE_ATTEMPTS = 3


def get_db_secrets(secrets_key: str = "neon_db") -> Optional[Dict[str, Any]]:
    """
    Retrieve database connection secrets from Streamlit secrets.
    
    This function extracts database configuration from st.secrets and returns
    it as a dictionary. It validates that all required parameters are present.
    
    Args:
        secrets_key (str): The key in st.secrets where database config is stored.
                          Defaults to "neon_db".
    
    Returns:
        Optional[Dict[str, Any]]: Dictionary containing database connection parameters:
            - user: Database username
            - password: Database password
            - host: Database hostname
            - database: Database name
            - port: Database port (defaults to 5432 if not provided)
        Returns None if secrets are not found or incomplete.
    
    Raises:
        ValueError: If required database parameters are missing.
    
    Example:
        >>> db_config = get_db_secrets()
        >>> if db_config:
        ...     print(f"Connecting to {db_config['host']}")
    """
    try:
        db_config = st.secrets.get(secrets_key)
        if not db_config:
            raise ValueError(f"{secrets_key} configuration not found in secrets.toml")
        
        # Extract connection details
        db_user = db_config.get("user")
        db_password = db_config.get("password")
        db_host = db_config.get("host")
        db_name = db_config.get("database")
        db_port = db_config.get("port", 5432)
        
        if not all([db_user, db_password, db_host, db_name]):
            raise ValueError("Missing required database connection parameters in secrets.toml")
        
        return {
            "user": db_user,
            "password": db_password,
            "host": db_host,
            "database": db_name,
            "port": db_port
        }
    except Exception as e:
        raise ValueError(f"Error retrieving database secrets: {str(e)}")


def get_table_name(secrets_key: str = "neon_db", default_table_name: str = "bse_uhs") -> str:
    """
    Retrieve the table name from Streamlit secrets.
    
    This function extracts the TABLE_NAME from the database configuration in st.secrets.
    If not found, it returns the default table name.
    
    Args:
        secrets_key (str): The key in st.secrets where database config is stored.
                          Defaults to "neon_db".
        default_table_name (str): Default table name to use if not found in secrets.
                                 Defaults to "bse_uhs".
    
    Returns:
        str: The table name from secrets, or the default if not found.
    
    Example:
        >>> table_name = get_table_name()
        >>> print(f"Using table: {table_name}")
    """
    try:
        db_config = st.secrets.get(secrets_key)
        if db_config and "TABLE_NAME" in db_config:
            return db_config.get("TABLE_NAME", default_table_name)
        return default_table_name
    except Exception:
        return default_table_name


def get_chat_threads_table_name(secrets_key: str = "neon_db") -> str:
    """Return the Neon table used for user-visible chat history threads."""
    try:
        db_config = st.secrets.get(secrets_key)
        if db_config and "CHAT_THREADS_TABLE_NAME" in db_config:
            return db_config.get("CHAT_THREADS_TABLE_NAME")
    except Exception:
        pass
    return f"{get_table_name(secrets_key)}_chat_threads"


def _safe_index_name(value: str, suffix: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in value.lower()).strip("_")
    return f"idx_{cleaned}_{suffix}"[:63]


def _thread_owner_clause() -> str:
    """Thread ownership is email-scoped."""
    return "lower(user_email) = lower(%s)"


def _thread_owner_params(user_email: str) -> tuple:
    """Return params for email-scoped thread ownership."""
    return (user_email,)


def _get_pool(secrets_key: str = "neon_db") -> Optional[pool.SimpleConnectionPool]:
    """Create or return the module-level connection pool. Returns None on failure."""
    global _connection_pool
    if _connection_pool is not None:
        return _connection_pool
    try:
        db_config = get_db_secrets(secrets_key)
        if not db_config:
            return None
        connection_string = _build_connection_string(db_config)
        _connection_pool = pool.SimpleConnectionPool(1, 5, dsn=connection_string)
        return _connection_pool
    except Exception:
        return None


def _build_connection_string(db_config: Dict[str, Any]) -> str:
    """Build a Neon PostgreSQL DSN with SSL and TCP keepalive settings."""
    return (
        f"postgresql://{db_config['user']}:{db_config['password']}"
        f"@{db_config['host']}:{db_config['port']}/{db_config['database']}"
        f"?sslmode=require&channel_binding=require"
        f"&keepalives=1&keepalives_idle=30&keepalives_interval=10&keepalives_count=5"
    )


def _ping_connection(conn: psycopg2.extensions.connection) -> bool:
    """Return True when the connection can execute a trivial query."""
    if conn.closed:
        return False
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
        return True
    except _CONNECTION_ERRORS:
        return False


def _discard_connection(conn: Optional[psycopg2.extensions.connection]) -> None:
    """Close a connection instead of returning it to the pool."""
    if conn is None:
        return
    if _connection_pool is not None:
        try:
            _connection_pool.putconn(conn, close=True)
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
    else:
        try:
            conn.close()
        except Exception:
            pass


def _safe_rollback(conn: Optional[psycopg2.extensions.connection]) -> None:
    """Rollback an open transaction without raising if the connection is dead."""
    if conn is None or conn.closed:
        return
    try:
        conn.rollback()
    except _CONNECTION_ERRORS:
        pass


def _return_connection(
    conn: Optional[psycopg2.extensions.connection],
    *,
    discard: bool = False,
) -> None:
    """Return a connection to the pool or close it if not using a pool."""
    if conn is None:
        return
    if discard or conn.closed:
        _discard_connection(conn)
        return
    if _connection_pool is not None:
        try:
            _connection_pool.putconn(conn)
        except Exception:
            _discard_connection(conn)
    else:
        try:
            conn.close()
        except Exception:
            pass


def get_neon_connection(secrets_key: str = "neon_db") -> Optional[psycopg2.extensions.connection]:
    """
    Obtain a connection to the Neon PostgreSQL database (from pool when available).
    
    Stale pooled connections are detected with a lightweight ping and discarded.
    Callers must call _return_connection(conn) in a finally block instead of conn.close().
    """
    pool_ref = _get_pool(secrets_key)
    if pool_ref is not None:
        for _ in range(_POOL_ACQUIRE_ATTEMPTS):
            try:
                conn = pool_ref.getconn()
            except Exception:
                return None
            if _ping_connection(conn):
                return conn
            _discard_connection(conn)
        return None
    try:
        db_config = get_db_secrets(secrets_key)
        if not db_config:
            return None
        return psycopg2.connect(_build_connection_string(db_config))
    except Exception:
        return None


def create_chat_table(table_name: Optional[str] = None) -> bool:
    """
    Create the database table if it doesn't exist (defaults to "bse_uhs" if not specified in secrets).
    
    This function creates a table for storing chatbot interaction logs with the
    following schema:
        - id: SERIAL PRIMARY KEY
        - user_email: VARCHAR(255) NOT NULL
        - user_name: VARCHAR(255) NULL
        - user_nickname: VARCHAR(255) NULL
        - timestamp: TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        - user_prompt: TEXT NOT NULL
        - llm_response_snippet: TEXT NOT NULL
        - feedback: INTEGER DEFAULT NULL
    
    Args:
        table_name (Optional[str]): Name of the table to create. If None, retrieves from st.secrets.
                                   Defaults to None (uses value from secrets or "bse").
    
    Returns:
        bool: True if table was created successfully or already exists, False otherwise.
    
    Example:
        >>> if create_chat_table():
        ...     print("Table ready for use")
    """
    if table_name is None:
        table_name = get_table_name()
    
    conn = get_neon_connection()
    if not conn:
        return False
    
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS "{table_name}" (
                    id SERIAL PRIMARY KEY,
                    user_email VARCHAR(255) NOT NULL,
                    user_name VARCHAR(255) NULL,
                    user_nickname VARCHAR(255) NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    user_prompt TEXT NOT NULL,
                    llm_response_snippet TEXT NOT NULL,
                    feedback INTEGER DEFAULT 0,
                    model VARCHAR(255) NULL,
                    attachment_names TEXT NULL
                );
                
                -- Ensure model column exists if table was already created
                DO $$ 
                BEGIN 
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                   WHERE table_name='{table_name}' AND column_name='model') THEN 
                        ALTER TABLE "{table_name}" ADD COLUMN model VARCHAR(255) NULL; 
                    END IF; 
                END $$;
                -- Ensure attachment_names column exists if table was already created
                DO $$ 
                BEGIN 
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                   WHERE table_name='{table_name}' AND column_name='attachment_names') THEN 
                        ALTER TABLE "{table_name}" ADD COLUMN attachment_names TEXT NULL; 
                    END IF; 
                END $$;
            """)
            conn.commit()
            return True
    except Exception:
        _safe_rollback(conn)
        return False
    finally:
        _return_connection(conn)


def create_user_chat_threads_table(table_name: Optional[str] = None) -> bool:
    """Create the user-visible chat-thread table if it does not already exist."""
    if table_name is None:
        table_name = get_chat_threads_table_name()

    conn = get_neon_connection()
    if not conn:
        return False

    user_updated_index = _safe_index_name(table_name, "user_updated")
    user_active_index = _safe_index_name(table_name, "user_active")

    try:
        with conn.cursor() as cursor:
            cursor.execute(f"""
                CREATE EXTENSION IF NOT EXISTS pgcrypto;

                CREATE TABLE IF NOT EXISTS "{table_name}" (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_email TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT 'New chat',
                    title_status TEXT NOT NULL DEFAULT 'pending',
                    recents JSONB NOT NULL DEFAULT '[]'::jsonb,
                    log_entry_ids JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    last_message_at TIMESTAMPTZ NULL,
                    archived_at TIMESTAMPTZ NULL
                );

                ALTER TABLE "{table_name}"
                    DROP COLUMN IF EXISTS user_sub;
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM information_schema.columns
                               WHERE table_schema = 'public'
                                 AND table_name = '{table_name}'
                                 AND column_name = 'user_name') THEN
                        ALTER TABLE "{table_name}" DROP COLUMN user_name;
                    END IF;
                END $$;
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM information_schema.columns
                               WHERE table_schema = 'public'
                                 AND table_name = '{table_name}'
                                 AND column_name = 'messages')
                       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                                       WHERE table_schema = 'public'
                                         AND table_name = '{table_name}'
                                         AND column_name = 'recents') THEN
                        ALTER TABLE "{table_name}" RENAME COLUMN messages TO recents;
                    END IF;
                    IF EXISTS (SELECT 1 FROM information_schema.columns
                               WHERE table_schema = 'public'
                                 AND table_name = '{table_name}'
                                 AND column_name = 'messages')
                       AND EXISTS (SELECT 1 FROM information_schema.columns
                                   WHERE table_schema = 'public'
                                     AND table_name = '{table_name}'
                                     AND column_name = 'recents') THEN
                        UPDATE "{table_name}" SET recents = messages;
                        ALTER TABLE "{table_name}" DROP COLUMN messages;
                    END IF;
                    IF EXISTS (SELECT 1 FROM information_schema.columns
                               WHERE table_schema = 'public'
                                 AND table_name = '{table_name}'
                                 AND column_name = 'chat_history') THEN
                        ALTER TABLE "{table_name}" DROP COLUMN chat_history;
                    END IF;
                    IF EXISTS (SELECT 1 FROM information_schema.columns
                               WHERE table_schema = 'public'
                                 AND table_name = '{table_name}'
                                 AND column_name = 'collected_answers') THEN
                        ALTER TABLE "{table_name}" DROP COLUMN collected_answers;
                    END IF;
                    IF EXISTS (SELECT 1 FROM information_schema.columns
                               WHERE table_schema = 'public'
                                 AND table_name = '{table_name}'
                                 AND column_name = 'model') THEN
                        ALTER TABLE "{table_name}" DROP COLUMN model;
                    END IF;
                END $$;

                CREATE INDEX IF NOT EXISTS "{user_updated_index}"
                    ON "{table_name}" (user_email, updated_at DESC);

                CREATE INDEX IF NOT EXISTS "{user_active_index}"
                    ON "{table_name}" (user_email, archived_at)
                    WHERE archived_at IS NULL;
            """)
            conn.commit()
            return True
    except Exception:
        _safe_rollback(conn)
        return False
    finally:
        _return_connection(conn)


def create_chat_thread(
    user_email: str,
    table_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Create a new empty user-visible chat thread."""
    if table_name is None:
        table_name = get_chat_threads_table_name()
    conn = get_neon_connection()
    if not conn:
        return None

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(f"""
                INSERT INTO "{table_name}"
                    (user_email)
                VALUES (%s)
                RETURNING *
            """, (user_email,))
            result = cursor.fetchone()
            conn.commit()
            return dict(result) if result else None
    except Exception:
        _safe_rollback(conn)
        return None
    finally:
        _return_connection(conn)


def list_chat_threads(
    user_email: str,
    include_archived: bool = False,
    limit: int = 25,
    offset: int = 0,
    table_name: Optional[str] = None,
) -> list[Dict[str, Any]]:
    """List chat threads for one user, newest first."""
    if table_name is None:
        table_name = get_chat_threads_table_name()
    conn = get_neon_connection()
    if not conn:
        return []

    archived_clause = "" if include_archived else "AND archived_at IS NULL"
    owner_clause = _thread_owner_clause()
    params = _thread_owner_params(user_email) + (limit, offset)

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(f"""
                SELECT
                    id,
                    user_email,
                    title,
                    title_status,
                    created_at,
                    updated_at,
                    last_message_at,
                    archived_at
                FROM "{table_name}"
                WHERE {owner_clause}
                  {archived_clause}
                ORDER BY updated_at DESC, created_at DESC
                LIMIT %s OFFSET %s
            """, params)
            return [dict(row) for row in cursor.fetchall()]
    except Exception:
        return []
    finally:
        _return_connection(conn)


def count_chat_threads(
    user_email: str,
    include_archived: bool = False,
    table_name: Optional[str] = None,
) -> int:
    """Count chat threads for one user."""
    if table_name is None:
        table_name = get_chat_threads_table_name()
    conn = get_neon_connection()
    if not conn:
        return 0

    archived_clause = "" if include_archived else "AND archived_at IS NULL"
    owner_clause = _thread_owner_clause()
    params = _thread_owner_params(user_email)

    try:
        with conn.cursor() as cursor:
            cursor.execute(f"""
                SELECT count(*)
                FROM "{table_name}"
                WHERE {owner_clause}
                  {archived_clause}
            """, params)
            result = cursor.fetchone()
            return int(result[0]) if result else 0
    except Exception:
        return 0
    finally:
        _return_connection(conn)


def read_chat_thread(
    thread_id: str,
    user_email: str,
    table_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Read one active chat thread if it belongs to the current user."""
    if table_name is None:
        table_name = get_chat_threads_table_name()
    conn = get_neon_connection()
    if not conn:
        return None

    owner_clause = _thread_owner_clause()
    params = (thread_id,) + _thread_owner_params(user_email)

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(f"""
                SELECT *
                FROM "{table_name}"
                WHERE id = %s
                  AND {owner_clause}
                  AND archived_at IS NULL
            """, params)
            result = cursor.fetchone()
            return dict(result) if result else None
    except Exception:
        return None
    finally:
        _return_connection(conn)


def update_chat_thread(
    thread_id: str,
    user_email: str,
    recents: list,
    log_entry_ids: dict,
    title: Optional[str] = None,
    title_status: Optional[str] = None,
    table_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Persist the UI/session state for one active chat thread."""
    if table_name is None:
        table_name = get_chat_threads_table_name()
    conn = get_neon_connection()
    if not conn:
        return None

    owner_clause = _thread_owner_clause()
    updates = [
        "recents = %s",
        "log_entry_ids = %s",
        "updated_at = now()",
        "last_message_at = CASE WHEN jsonb_array_length(%s::jsonb) > 0 THEN now() ELSE last_message_at END",
    ]
    values: list[Any] = [
        Json(recents),
        Json(log_entry_ids),
        Json(recents),
    ]

    if title is not None:
        updates.append("title = %s")
        values.append(title)
    if title_status is not None:
        updates.append("title_status = %s")
        values.append(title_status)

    values.extend((thread_id, *_thread_owner_params(user_email)))

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(f"""
                UPDATE "{table_name}"
                SET {', '.join(updates)}
                WHERE id = %s
                  AND {owner_clause}
                  AND archived_at IS NULL
                RETURNING *
            """, values)
            result = cursor.fetchone()
            conn.commit()
            return dict(result) if result else None
    except Exception:
        _safe_rollback(conn)
        return None
    finally:
        _return_connection(conn)


def rename_chat_thread(
    thread_id: str,
    user_email: str,
    title: str,
    table_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Rename one active chat thread owned by the current user."""
    if table_name is None:
        table_name = get_chat_threads_table_name()
    conn = get_neon_connection()
    if not conn:
        return None

    owner_clause = _thread_owner_clause()
    params = (title, thread_id, *_thread_owner_params(user_email))

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(f"""
                UPDATE "{table_name}"
                SET title = %s,
                    title_status = 'manual',
                    updated_at = now()
                WHERE id = %s
                  AND {owner_clause}
                  AND archived_at IS NULL
                RETURNING *
            """, params)
            result = cursor.fetchone()
            conn.commit()
            return dict(result) if result else None
    except Exception:
        _safe_rollback(conn)
        return None
    finally:
        _return_connection(conn)


def archive_chat_thread(
    thread_id: str,
    user_email: str,
    table_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Soft-delete one active chat thread owned by the current user."""
    if table_name is None:
        table_name = get_chat_threads_table_name()
    conn = get_neon_connection()
    if not conn:
        return None

    owner_clause = _thread_owner_clause()
    params = (thread_id, *_thread_owner_params(user_email))

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(f"""
                UPDATE "{table_name}"
                SET archived_at = now(),
                    updated_at = now()
                WHERE id = %s
                  AND {owner_clause}
                  AND archived_at IS NULL
                RETURNING *
            """, params)
            result = cursor.fetchone()
            conn.commit()
            return dict(result) if result else None
    except Exception:
        _safe_rollback(conn)
        return None
    finally:
        _return_connection(conn)


def create_log_entry(
    user_email: str,
    user_prompt: str,
    llm_response_snippet: str,
    feedback: Optional[int] = 0,
    user_name: Optional[str] = None,
    user_nickname: Optional[str] = None,
    model: Optional[str] = None,
    attachment_names: Optional[str] = None,
    table_name: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Create a new chatbot log entry in the database.
    
    This function inserts a new log entry into the database table and returns
    the created record with its generated ID and timestamp.
    
    Args:
        user_email (str): Email address of the user who made the prompt.
        user_prompt (str): The user's prompt to the chatbot.
        llm_response_snippet (str): The LLM's response to the user's prompt.
        feedback (Optional[int]): User feedback value (0 = no feedback, 1 = thumbs down, 2 = thumbs up).
                                 Defaults to 0.
        user_name (Optional[str]): User's full name. Defaults to None.
        user_nickname (Optional[str]): User's nickname. Defaults to None.
        attachment_names (Optional[str]): Comma-separated filenames of attachments, if any. Defaults to None.
        table_name (Optional[str]): Name of the table to insert into. If None, retrieves from st.secrets.
                                   Defaults to None (uses value from secrets or "bse-document-analysis").
    
    Returns:
        Optional[Dict[str, Any]]: Dictionary containing the created log entry with keys:
            - id: Auto-generated log entry ID
            - user_email: User's email
            - user_name: User's name (if provided)
            - user_nickname: User's nickname (if provided)
            - timestamp: Timestamp when entry was created
            - user_prompt: User's prompt
            - llm_response_snippet: LLM response
            - feedback: Feedback value (if provided)
        Returns None if creation fails.
    
    Example:
        >>> log_entry = create_log_entry(
        ...     user_email="user@example.com",
        ...     user_prompt="What is Python?",
        ...     llm_response_snippet="Python is a programming language...",
        ...     feedback=1,
        ...     user_name="John Doe",
        ...     user_nickname="johndoe"
        ... )
        >>> if log_entry:
        ...     print(f"Created log entry with ID: {log_entry['id']}")
    """
    if table_name is None:
        table_name = get_table_name()
    
    conn = get_neon_connection()
    if not conn:
        return None
    
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(f"""
                INSERT INTO "{table_name}" (user_email, user_name, user_nickname, user_prompt, llm_response_snippet, feedback, model, attachment_names)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (user_email, user_name, user_nickname, user_prompt, llm_response_snippet, feedback, model, attachment_names))
            result = cursor.fetchone()
            conn.commit()
            return dict(result) if result else None
    except Exception:
        _safe_rollback(conn)
        return None
    finally:
        _return_connection(conn)


def read_log_by_id(log_id: int, table_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Read a specific log entry by its ID.
    
    This function retrieves a single log entry from the database using its
    unique ID.
    
    Args:
        log_id (int): The ID of the log entry to retrieve.
        table_name (Optional[str]): Name of the table to read from. If None, retrieves from st.secrets.
                                   Defaults to None (uses value from secrets or "bse-document-analysis").
    
    Returns:
        Optional[Dict[str, Any]]: Dictionary containing the log entry if found,
                                 None if not found or on error.
    
    Example:
        >>> log = read_log_by_id(123)
        >>> if log:
        ...     print(f"Found log: {log['user_prompt']}")
        ... else:
        ...     print("Log not found")
    """
    if table_name is None:
        table_name = get_table_name()
    
    conn = get_neon_connection()
    if not conn:
        return None
    
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(f'SELECT * FROM "{table_name}" WHERE id = %s', (log_id,))
            result = cursor.fetchone()
            return dict(result) if result else None
    except Exception:
        return None
    finally:
        _return_connection(conn)


def update_log(
    log_id: int,
    feedback: Optional[int] = None,
    user_prompt: Optional[str] = None,
    llm_response_snippet: Optional[str] = None,
    table_name: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Update a log entry in the database.
    
    This function updates one or more fields of an existing log entry.
    At least one field must be provided for update.
    
    Args:
        log_id (int): The ID of the log entry to update.
        feedback (Optional[int]): New feedback value (0 = no feedback, 1 = thumbs down, 2 = thumbs up).
        user_prompt (Optional[str]): New user prompt text.
        llm_response_snippet (Optional[str]): New LLM response text.
        table_name (Optional[str]): Name of the table to update. If None, retrieves from st.secrets.
                                   Defaults to None (uses value from secrets or "bse-document-analysis").
    
    Returns:
        Optional[Dict[str, Any]]: Dictionary containing the updated log entry,
                                 None if update fails or no fields provided.
    
    Example:
        >>> # Update only feedback
        >>> updated = update_log(log_id=123, feedback=2)
        
        >>> # Update multiple fields
        >>> updated = update_log(
        ...     log_id=123,
        ...     feedback=2,
        ...     user_prompt="Updated prompt"
        ... )
    """
    if table_name is None:
        table_name = get_table_name()
    
    conn = get_neon_connection()
    if not conn:
        return None
    
    # Build update query dynamically based on provided fields
    updates = []
    values = []
    
    if feedback is not None:
        updates.append("feedback = %s")
        values.append(feedback)
    if user_prompt is not None:
        updates.append("user_prompt = %s")
        values.append(user_prompt)
    if llm_response_snippet is not None:
        updates.append("llm_response_snippet = %s")
        values.append(llm_response_snippet)
    
    if not updates:
        return None
    
    values.append(log_id)
    
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(f"""
                UPDATE "{table_name}" 
                SET {', '.join(updates)}
                WHERE id = %s 
                RETURNING *
            """, values)
            result = cursor.fetchone()
            conn.commit()
            return dict(result) if result else None
    except Exception:
        _safe_rollback(conn)
        return None
    finally:
        _return_connection(conn)

