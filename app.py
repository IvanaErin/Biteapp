import os
import base64
import streamlit as st
import pandas as pd
import snowflake.connector
from groq import Groq
import random
from datetime import datetime, date, time
import matplotlib.pyplot as plt
import hashlib
import secrets
import re
from PIL import Image
import json

# Try to import st_autorefresh helper if available
try:
    from streamlit_autorefresh import st_autorefresh
    _HAS_ST_AUTORELOAD = True
except Exception:
    # fallback: Streamlit may provide st.autorefresh in some versions
    _HAS_ST_AUTORELOAD = hasattr(st, "autorefresh")

# ---------------------------
# AI CLIENT
# ---------------------------
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
except Exception:
    client = None

# ---------------------------
# PAGE CONFIG & BACKGROUND
# ---------------------------
st.set_page_config(page_title="BiteHub Canteen GenAI", layout="wide")

def set_background(image_file: str | None = None):
    css_parts = []
    if image_file and os.path.exists(image_file):
        with open(image_file, "rb") as f:
            encoded = base64.b64encode(f.read()).decode()
        ext = image_file.split(".")[-1].lower()
        mime = "jpeg" if ext in ["jpg", "jpeg"] else "png"
        css_parts.append(
            f"""
            [data-testid="stAppViewContainer"] {{
                background: url("data:image/{mime};base64,{encoded}");
                background-size: cover;
                background-position: center;
                background-repeat: no-repeat;
            }}
            """
        )

    css_parts.append(
        """
        [data-testid="stAppViewContainer"] > section:first-child {
            padding-top: 18px !important;
            margin-top: 0px !important;
        }
        #MainMenu { visibility: hidden; }
        footer { visibility: hidden; }
        .login-card {
            background: rgba(10,10,10,0.6);
            padding: 1.6rem;
            border-radius: 12px;
            max-width: 840px;
            margin: 18px auto;
            color: #fff;
            box-shadow: 0 8px 28px rgba(0,0,0,0.5);
        }
        div.stButton > button {
            width: 100%;
            height: 44px;
            font-size: 15px;
            border-radius: 8px;
        }
        .stTextInput>div>div>input, .stTextInput>div>div>div>input {
            background: rgba(0,0,0,0.55);
            color: #fff;
        }
        .stContainer, .stMarkdown, .stExpander {
            color: #fff;
        }
        """
    )

    st.markdown("<style>" + "\n".join(css_parts) + "</style>", unsafe_allow_html=True)

set_background("back.jpg")

# ---------------------------
# SNOWFLAKE CONNECTION
# ---------------------------
def get_connection():
    try:
        return snowflake.connector.connect(
            user=st.secrets["SNOWFLAKE_USER"],
            password=st.secrets["SNOWFLAKE_PASSWORD"],
            account=st.secrets["SNOWFLAKE_ACCOUNT"],
            warehouse=st.secrets.get("SNOWFLAKE_WAREHOUSE"),
            database=st.secrets.get("SNOWFLAKE_DATABASE"),
            schema=st.secrets.get("SNOWFLAKE_SCHEMA"),
        )
    except Exception:
        return None

def get_snowflake_conn():
    try:
        return snowflake.connector.connect(
            user=st.secrets["SNOWFLAKE_USER"],
            password=st.secrets["SNOWFLAKE_PASSWORD"],
            account=st.secrets["SNOWFLAKE_ACCOUNT"],
            warehouse=st.secrets["SNOWFLAKE_WAREHOUSE"],
            database=st.secrets["SNOWFLAKE_DATABASE"],
            schema=st.secrets["SNOWFLAKE_SCHEMA"]
        )
    except Exception:
        return None

# ---------------------------
# PASSWORD HELPERS
# ---------------------------
def hash_password(password: str, salt: bytes | None = None) -> str:
    if salt is None:
        salt = secrets.token_bytes(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 150_000)
    return salt.hex() + "$" + hashed.hex()

def verify_password(stored: str, provided_password: str) -> bool:
    try:
        salt_hex, h = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expected = hashlib.pbkdf2_hmac("sha256", provided_password.encode(), salt, 150_000)
        return expected.hex() == h
    except Exception:
        return False

# ---------------------------
# LOCAL FALLBACK
# ---------------------------
def _ensure_local_db():
    if "_local_accounts" not in st.session_state:
        st.session_state._local_accounts = {}
    if "_local_feedbacks" not in st.session_state:
        st.session_state._local_feedbacks = []
    if "_local_receipts" not in st.session_state:
        st.session_state._local_receipts = []
    if "notifications" not in st.session_state:
        st.session_state.notifications = []

# ---------------------------
# ACCOUNTS
# ---------------------------
def save_account(username: str, password: str, role: str = "Non-Staff"):
    conn = get_connection()
    if not conn:
        _ensure_local_db()
        st.session_state._local_accounts[username] = {
            "password": password,
            "role": role,
            "loyalty_points": 0
        }
        return
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (username, password, role) VALUES (%s, %s, %s)",
            (username, password, role)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()

def get_account(username: str):
    conn = get_connection()
    if not conn:
        _ensure_local_db()
        return st.session_state._local_accounts.get(username)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, username, password, role, loyalty_points FROM users WHERE username=%s",
            (username,)
        )
        row = cur.fetchone()
        if row:
            return {
                "id": row[0],
                "username": row[1],
                "password": row[2],
                "role": row[3],
                "loyalty_points": row[4]
            }
        return None
    finally:
        cur.close()
        conn.close()

def validate_account(username: str, password: str):
    acc = get_account(username)
    if acc and verify_password(acc["password"], password):
        return acc
    return None

def user_exists(username: str) -> bool:
    conn = get_connection()
    if not conn:
        _ensure_local_db()
        return username in st.session_state._local_accounts
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(1) FROM users WHERE username=%s", (username,))
        r = cur.fetchone()
        return bool(r and r[0] > 0)
    finally:
        cur.close()
        conn.close()

# ---------------------------
# RECEIPTS (normalize items before saving)
# ---------------------------
# ---------------------------
# MENU MANAGEMENT (FIXED)
# ---------------------------
def load_menu():
    conn = get_connection()
    if not conn:
        return pd.DataFrame(columns=["CATEGORY", "ITEM", "PRICE"])
    try:
        cur = conn.cursor()
        cur.execute("SELECT CATEGORY, ITEM, PRICE FROM MENU ORDER BY CATEGORY, ITEM")
        df = cur.fetch_pandas_all()
        df["PRICE"] = pd.to_numeric(df.get("PRICE", 0), errors="coerce").fillna(0)
        return df
    except:
        return pd.DataFrame(columns=["CATEGORY", "ITEM", "PRICE"])
    finally:
        cur.close()
        conn.close()

def upsert_menu(df: pd.DataFrame):
    if df.empty:
        st.warning("Menu is empty. Nothing to save.")
        return
    df = df.fillna("")
    df["PRICE"] = pd.to_numeric(df["PRICE"], errors="coerce").fillna(0)
    conn = get_connection()
    if not conn:
        st.error("Database connection failed. Cannot save menu.")
        return
    try:
        cur = conn.cursor()
        for _, row in df.iterrows():
            cat = str(row["CATEGORY"]).replace("'", "''")
            item = str(row["ITEM"]).replace("'", "''")
            price = row["PRICE"]
            sql = f"""
                MERGE INTO MENU AS target
                USING (SELECT '{cat}' AS CATEGORY, '{item}' AS ITEM, {price} AS PRICE) AS source
                ON target.CATEGORY = source.CATEGORY AND target.ITEM = source.ITEM
                WHEN MATCHED THEN UPDATE SET target.PRICE = source.PRICE
                WHEN NOT MATCHED THEN INSERT (CATEGORY, ITEM, PRICE) VALUES (source.CATEGORY, source.ITEM, source.PRICE)
            """
            cur.execute(sql)
        conn.commit()
        st.success("✅ Menu updated successfully!")
    except Exception as e:
        st.error(f"❌ Error updating menu: {e}")
    finally:
        cur.close()
        conn.close()

def manage_menu():
    st.subheader("📖 Manage Menu")
    menu_df = load_menu()
    if not menu_df.empty:
        edited = st.data_editor(menu_df, num_rows="dynamic")
        if st.button("Save Menu Updates"):
            upsert_menu(edited)
            st.experimental_rerun()
    else:
        st.info("No menu items available.")
        
def _normalize_items_for_receipt(items):
    """
    Accepts many formats and returns a list of dicts:
    [{"name": ..., "qty": int, "price": float, "category": ...}, ...]
    """
    normalized = []
    cat_map = _build_menu_category_map()

    # If items is a dict stringified, try to parse
    if isinstance(items, str):
        try:
            parsed = json.loads(items)
        except Exception:
            # fallback: try eval (only if local, not ideal), but avoid for security in production
            try:
                parsed = eval(items)
            except Exception:
                parsed = items
    else:
        parsed = items

    # If parsed is dict like {"Donut": 1} or {"Donut": {"qty":1,...}}
    if isinstance(parsed, dict):
        for k, v in parsed.items():
            name = str(k)
            if isinstance(v, dict):
                qty = int(v.get("qty", 1))
                price = float(v.get("price", 0.0)) if v.get("price") is not None else 0.0
            else:
                # v could be integer qty
                try:
                    qty = int(v)
                except Exception:
                    qty = 1
                price = 0.0
            category = cat_map.get(name.lower()) or "Uncategorized"
            normalized.append({"name": name, "qty": qty, "price": price, "category": category})

    # If parsed is list of dicts: [{"name":..., "qty":..., "price":...}, ...]
    elif isinstance(parsed, list):
        for it in parsed:
            if not isinstance(it, dict):
                continue
            # various key possibilities
            name = it.get("name") or it.get("ITEM_NAME") or it.get("item") or "Unknown"
            try:
                qty = int(it.get("qty") or it.get("QUANTITY") or it.get("quantity") or 1)
            except Exception:
                qty = 1
            try:
                price = float(it.get("price") or it.get("PRICE") or 0.0)
            except Exception:
                price = 0.0
            category = it.get("category") or cat_map.get(str(name).lower()) or "Uncategorized"
            normalized.append({"name": name, "qty": qty, "price": price, "category": category})

    # If parsed is a single item's structure (like {"name":..., ...})
    elif isinstance(parsed, (int, float, str)):
        # not a structured item; ignore or treat as unknown
        pass
    else:
        # unknown structure - attempt best-effort convert if it's a mapping-like
        try:
            for k, v in dict(parsed).items():
                name = str(k)
                if isinstance(v, dict):
                    qty = int(v.get("qty", 1))
                    price = float(v.get("price", 0.0)) if v.get("price") is not None else 0.0
                else:
                    try:
                        qty = int(v)
                    except Exception:
                        qty = 1
                    price = 0.0
                category = cat_map.get(name.lower()) or "Uncategorized"
                normalized.append({"name": name, "qty": qty, "price": price, "category": category})
        except Exception:
            pass

    return normalized

def delete_menu_items(items):
    if not items:
        return
    conn = get_snowflake_conn()
    cur = conn.cursor()
    # escape single quotes in item names
    items_str = ",".join([f"'{i.replace('\'', '\'\'')}'" for i in items])
    sql = f"DELETE FROM MENU WHERE ITEM IN ({items_str})"
    cur.execute(sql)
    conn.commit()
    cur.close()
    conn.close()

def save_receipt(order_id, items, total, payment_method, user_id, pickup_dt, status="Pending"):
    """
    Save a receipt. Ensures `items` are normalized to a consistent JSON list format.
    """
    # Normalize items (handle cart dicts or mixed formats)
    normalized_items = _normalize_items_for_receipt(items)

    # If items were passed as session cart dict {name: {qty, price}}, normalize too
    if not normalized_items and isinstance(items, dict):
        for name, info in items.items():
            try:
                qty = int(info.get("qty", 1))
            except Exception:
                qty = 1
            try:
                price = float(info.get("price", 0.0))
            except Exception:
                price = 0.0
            cat = _build_menu_category_map().get(name.lower()) or "Uncategorized"
            normalized_items.append({"name": name, "qty": qty, "price": price, "category": cat})

    items_json = json.dumps(normalized_items)

    conn = get_connection()
    if not conn:
        _ensure_local_db()
        if "_local_receipts" not in st.session_state:
            st.session_state._local_receipts = []
        st.session_state._local_receipts.append({
            "order_id": order_id,
            "items": items_json,
            "total": float(total),
            "payment_method": payment_method,
            "user_id": user_id,
            "pickup_time": datetime.strptime(pickup_dt, "%Y-%m-%d %H:%M"),
            "status": status,
            "timestamp": datetime.now()
        })
        return

    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO receipts 
            (order_id, items, total, payment_method, user_id, pickup_time, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                order_id,
                items_json,
                float(total),
                payment_method,
                user_id,
                datetime.strptime(pickup_dt, "%Y-%m-%d %H:%M"),
                status
            )
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()

def load_receipts_df():
    conn = get_connection()
    if not conn:
        _ensure_local_db()
        rows = st.session_state.get("_local_receipts", [])
        return pd.DataFrame(rows) if rows else pd.DataFrame(
            columns=["order_id","items","total","payment_method","user_id","pickup_time","status","timestamp"]
        )
    try:
        cur = conn.cursor()
        cur.execute("""
SELECT order_id, items, total, payment_method, user_id, pickup_time AS pickup_time, status, timestamp
FROM receipts
ORDER BY timestamp DESC
""")
        rows = cur.fetchall()
        return pd.DataFrame(rows, columns=["order_id","items","total","payment_method","user_id","pickup_time","status","timestamp"])
    finally:
        cur.close()
        conn.close()

def update_order_status(order_id: str, new_status: str):
    """
    Update order status in DB if available, otherwise update local fallback.
    """
    conn = get_connection()
    if not conn:
        _ensure_local_db()
        updated = False
        for r in st.session_state.get("_local_receipts", []):
            if r.get("order_id") == order_id:
                r["status"] = new_status
                updated = True
        return updated

    try:
        cur = conn.cursor()
        cur.execute("UPDATE receipts SET status = %s WHERE order_id = %s", (new_status, order_id))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        cur.close()
        conn.close()

# ---------------------------
# NOTIFICATIONS (DB-backed + session fallback)
# ---------------------------
def add_notification(user_id: str, message: str):
    """
    Persist notification to DB (if available) and append to session fallback.
    """
    _ensure_local_db()
    note_msg = f"{message}"
    timestamp = datetime.now()

    # Append to session fallback list of dicts for instant local visibility
    st.session_state.notifications.append({"user_id": user_id, "message": note_msg, "timestamp": timestamp})

    # Try to persist to DB notifications table (best-effort)
    conn = get_connection()
    if not conn:
        return
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO notifications (user_id, message, timestamp, is_read) VALUES (%s, %s, %s, %s)",
                (user_id, note_msg, timestamp, False)
            )
            conn.commit()
        except Exception:
            # ignore if notifications table doesn't exist or insert fails
            pass
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass

def get_notifications_for_user(user_id: str):
    """
    Return list of messages (strings) for the given user combining DB and session fallback.
    DB results are ordered newest first.
    """
    _ensure_local_db()
    results = []

    # First, try DB-backed notifications (unread)
    conn = get_connection()
    if conn:
        try:
            cur = conn.cursor()
            try:
                # Try to select unread notifications first; if no is_read column, fallback to messages
                cur.execute("""
SELECT message, timestamp FROM notifications
WHERE user_id = %s AND (is_read = FALSE OR is_read IS NULL)
ORDER BY timestamp DESC
""", (user_id,))
                rows = cur.fetchall()
                for r in rows:
                    msg = r[0]
                    ts = r[1] if len(r) > 1 else None
                    results.append(f"[{ts}] {msg}" if ts else msg)
            except Exception:
                # If notifications table schema differs, attempt generic select
                try:
                    cur.execute("SELECT message, timestamp FROM notifications WHERE user_id = %s ORDER BY timestamp DESC", (user_id,))
                    rows = cur.fetchall()
                    for r in rows:
                        msg = r[0]
                        ts = r[1] if len(r) > 1 else None
                        results.append(f"[{ts}] {msg}" if ts else msg)
                except Exception:
                    pass
        finally:
            try:
                cur.close()
                conn.close()
            except Exception:
                pass

    # Merge session notifications (they may be instant and not yet in DB)
    for n in st.session_state.get("notifications", []):
        if n.get("user_id") == user_id:
            ts = n.get("timestamp")
            if isinstance(ts, datetime):
                ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
            else:
                ts_str = str(ts)
            results.insert(0, f"[{ts_str}] {n.get('message')}")

    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for r in results:
        if r not in seen:
            deduped.append(r)
            seen.add(r)

    return deduped

def clear_notifications_for_user(user_id: str):
    """
    Mark notifications read in DB (if possible) and clear session fallback.
    """
    # Clear session fallback
    st.session_state.notifications = [n for n in st.session_state.get("notifications", []) if n.get("user_id") != user_id]

    # Mark DB notifications as read if possible
    conn = get_connection()
    if not conn:
        return
    try:
        cur = conn.cursor()
        try:
            # best-effort: set is_read true if column exists
            cur.execute("UPDATE notifications SET is_read = TRUE WHERE user_id = %s", (user_id,))
            conn.commit()
        except Exception:
            # try deleting older notifications (if update fails)
            try:
                cur.execute("DELETE FROM notifications WHERE user_id = %s", (user_id,))
                conn.commit()
            except Exception:
                pass
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass

# ---------------------------
# FEEDBACK
# ---------------------------
def save_feedback(item: str, feedback: str, rating: int, user_id: str):
    conn = get_connection()
    if not conn:
        _ensure_local_db()
        st.session_state._local_feedbacks.append({
            "item": item, "feedback": feedback, "rating": rating, "user_id": user_id, "timestamp": datetime.now()
        })
        return
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO feedbacks (item, feedback, rating, user_id) VALUES (%s, %s, %s, %s)",
            (item, feedback, rating, user_id)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()

def load_feedbacks_df():
    conn = get_connection()
    if not conn:
        _ensure_local_db()
        rows = st.session_state._local_feedbacks
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["item","feedback","rating","user_id","timestamp"])
    try:
        cur = conn.cursor()
        cur.execute("SELECT item, feedback, rating, user_id, timestamp FROM feedbacks ORDER BY timestamp DESC")
        rows = cur.fetchall()
        return pd.DataFrame(rows, columns=["item","feedback","rating","user_id","timestamp"])
    finally:
        cur.close()
        conn.close()

# ---------------------------
# MENU (with resilient column detection)
# ---------------------------
def detect_menu_columns(df: pd.DataFrame):
    # return tuple (category_col, item_col, price_col)
    cols = [c.upper() for c in df.columns.tolist()]
    category_col = None
    item_col = None
    price_col = None

    for c in df.columns:
        cu = c.upper()
        if category_col is None and ("CATEGORY" in cu or cu == "CAT"):
            category_col = c
        if item_col is None and ("ITEM" in cu or "NAME" in cu):
            item_col = c
        if price_col is None and ("PRICE" in cu or "COST" in cu or "AMOUNT" in cu):
            price_col = c

    # fallbacks
    if category_col is None:
        for c in df.columns:
            if "TYPE" in c.upper():
                category_col = c
                break
    if item_col is None:
        # prefer first column that isn't category or numeric price
        for c in df.columns:
            if c != category_col and not pd.api.types.is_numeric_dtype(df[c]):
                item_col = c
                break
    if price_col is None:
        # pick first numeric column
        for c in df.columns:
            if pd.api.types.is_numeric_dtype(df[c]):
                price_col = c
                break

    return category_col, item_col, price_col

def load_menu():
    conn = get_snowflake_conn()
    # if no connection, return a fallback DataFrame
    if not conn:
        default_menu = {
            "CATEGORY": ["Breakfast","Breakfast","Lunch","Lunch","Drinks","Drinks","Snacks","Snacks"],
            "ITEM": ["","","","","","","",""],
            "PRICE": [0]
        }
        return pd.DataFrame(default_menu)

    try:
        df = pd.read_sql("SELECT * FROM MENU ORDER BY CATEGORY, ITEM", conn)
        # ensure CATEGORY, ITEM, PRICE exist; if not try to insert defaults
        # if table exists but empty, populate defaults
        if df.empty:
            default_menu = {
                "CATEGORY": ["Breakfast","Breakfast","Lunch","Lunch","Drinks","Drinks","Snacks","Snacks"],
                "ITEM": ["Pancakes","Omelette","Burger","Pizza","Coffee","Juice","Chips","Donut"],
                "PRICE": [50,40,80,120,30,40,20,25]
            }
            cursor = conn.cursor()
            for cat, item, price in zip(default_menu["CATEGORY"], default_menu["ITEM"], default_menu["PRICE"]):
                try:
                    cursor.execute("INSERT INTO MENU (CATEGORY, ITEM, PRICE) VALUES (%s, %s, %s)", (cat, item, price))
                except Exception:
                    # ignore insertion errors (table might have different schema)
                    pass
            conn.commit()
            df = pd.read_sql("SELECT * FROM MENU ORDER BY CATEGORY, ITEM", conn)
        return df
    finally:
        conn.close()

def upsert_menu(df: pd.DataFrame):
    conn = get_snowflake_conn()
    if not conn:
        return
    try:
        cur = conn.cursor()
        for _, row in df.iterrows():
            # handle gracefully if columns missing
            category = row.get("CATEGORY", row.get("Category", None))
            item = row.get("ITEM", row.get("Item", None))
            price = row.get("PRICE", row.get("Price", None))
            if category is None or item is None or price is None:
                continue
            cur.execute("""
                MERGE INTO MENU AS target
                USING (SELECT %s AS CATEGORY, %s AS ITEM, %s AS PRICE) AS source
                ON target.CATEGORY = source.CATEGORY AND target.ITEM = source.ITEM
                WHEN MATCHED THEN
                    UPDATE SET PRICE = source.PRICE
                WHEN NOT MATCHED THEN
                    INSERT (CATEGORY, ITEM, PRICE) VALUES (source.CATEGORY, source.ITEM, source.PRICE)
            """, (category, item, price))
        conn.commit()
    finally:
        cur.close()
        conn.close()

# ---------------------------
# AI
# ---------------------------
def run_ai(question: str, extra_context: str = "") -> str:
    if not client:
        return "⚠️ AI unavailable (no Groq client configured)."
    if not question:
        return "Please ask a question."
    try:
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",  # ✅ currently supported by Groq
            messages=[
                {"role": "system", "content": "You are BiteHub's smart assistant. Answer questions about the canteen, menu, meals, prices, and food items only."},
                {"role": "user", "content": question + "\n" + extra_context}
            ]
        )
        return resp.choices[0].message.content
    except Exception as e:
        return f"⚠️ AI unavailable: {e}"

# ---------------------------
# SESSION DEFAULTS
# ---------------------------
if "page" not in st.session_state:
    st.session_state.page = "login"
if "user" not in st.session_state:
    st.session_state.user = None
if "cart" not in st.session_state:
    # cart as dict: { item_name: {"qty": int, "price": float} }
    st.session_state.cart = {}
if "notifications" not in st.session_state:
    st.session_state.notifications = []
if "_local_accounts" not in st.session_state:
    st.session_state._local_accounts = {}
if "_local_feedbacks" not in st.session_state:
    st.session_state._local_feedbacks = []
if "_local_receipts" not in st.session_state:
    st.session_state._local_receipts = []

# ---------------------------
# PASSWORD RULES
# ---------------------------
def password_valid_rules(pw: str):
    rules = {
        "length": len(pw) >= 12,
        "upper": bool(re.search(r"[A-Z]", pw)),
        "lower": bool(re.search(r"[a-z]", pw)),
        "digit": bool(re.search(r"[0-9]", pw)),
        "symbol": bool(re.search(r"[^\w\s]", pw)),
    }
    return rules

# ---------------------------
# LOGIN PAGE
# ---------------------------
if st.session_state.page == "login":
    st.markdown(
        """
        <h1 style='text-align: center; color: #FF6F61; font-size: 60px; margin-top: 20px;'>☕ BiteHub</h1>
        <p style='text-align: center; color: #888888; font-size: 18px;'>Welcome! Please log in below.</p>
        """,
        unsafe_allow_html=True
    )

    username = st.text_input("Username", placeholder="Enter username", key="login_username")
    password = st.text_input("Password", type="password", placeholder="Enter password", key="login_password")

    col1, col2, col3, col4, col5 = st.columns([1, 2, 2, 2, 1])
    with col2:
        if st.button("🔑 Log In", use_container_width=True):
            acc = get_account(username)
            if acc and verify_password(acc["password"], password):
                role_value = acc.get("role", "Guest")
                try:
                    normalized_role = str(role_value).strip().capitalize()
                except Exception:
                    normalized_role = "Guest"
                acc["role"] = normalized_role
                st.session_state.user = acc
                st.session_state.page = "main"
                st.success(f"✅ Welcome {acc['username']}!")
                st.rerun()
            else:
                st.error("❌ Invalid username or password.")

    with col3:
        if st.button("🎟️ Guest Account", use_container_width=True):
            st.session_state.user = {"username": "Guest", "role": "Guest", "loyalty_points": 0, "cart": []}
            st.session_state.page = "main"
            st.rerun()

    with col4:
        if st.button("📝 Create Account", use_container_width=True):
            st.session_state.page = "signup"
            st.rerun()


# ---------------------------
# SIGNUP PAGE
# ---------------------------
elif st.session_state.page == "signup":
    st.markdown("<h1 style='text-align: center; color: #FF6F61;'>📝 BiteHub — Sign Up</h1>", unsafe_allow_html=True)

    new_username = st.text_input("Create Username", key="new_user")
    new_password = st.text_input("Create Password", type="password", key="new_pass")
    confirm_password = st.text_input("Confirm Password", type="password", key="conf_pass")

    if st.button("✅ Register Account"):
        if not new_username or not new_password:
            st.warning("⚠️ Please fill in all fields.")
        elif new_password != confirm_password:
            st.warning("⚠️ Passwords do not match.")
        elif get_account(new_username):
            st.error("⚠️ Username already exists.")
        else:
            save_account(new_username, new_password, role="User")
            st.success("🎉 Account created successfully! Please log in.")
            st.session_state.page = "login"
            st.rerun()

    if st.button("⬅️ Back to Login"):
        st.session_state.page = "login"
        st.rerun()


# ---------------------------
# MAIN PORTAL (Staff / Non-Staff / Guest)
# ---------------------------
elif st.session_state.page == "main":
    if "user" not in st.session_state or not st.session_state.user:
        st.session_state.page = "login"
        st.rerun()

    user = st.session_state.user
    role = str(user.get("role", "Guest")).capitalize()
    user["role"] = role
    is_guest = (role == "Guest")

    # ---------------------------
    # STAFF PORTAL
    # ---------------------------
    if role == "Staff":
        st.session_state.staff_choice = st.sidebar.radio(
            "Staff Menu",
            ["Dashboard", "Pending Orders", "Manage Menu", "AI Assistant", "Feedback Review", "Sales Report"],
            index=["Dashboard", "Pending Orders", "Manage Menu", "AI Assistant", "Feedback Review", "Sales Report"].index(
                st.session_state.get("staff_choice", "Dashboard")
            )
        )

        choice = st.session_state.staff_choice

        if choice == "Dashboard":
            st.subheader("📊 Staff Dashboard")
            st.info("Metrics and KPIs coming soon.")

        elif choice == "Pending Orders":
            st.subheader("📦 Pending Orders")
            receipts = load_receipts_df()
            pending_orders = receipts[receipts["status"] == "Pending"] if not receipts.empty else pd.DataFrame()
            if not pending_orders.empty:
                for idx, row in pending_orders.iterrows():
                    order_id = row.get("order_id")
                    st.markdown(f"**Order ID:** {order_id} | **User:** {row.get('user_id')} | **Payment:** {row.get('payment_method')}")
                    st.write("Items:", row.get("items"))
                    if st.button("✅ Mark as Ready", key=f"ready_{order_id}"):
                        update_order_status(order_id, "Ready")
                        add_notification(row.get("user_id"), f"Your order #{order_id} is ready for pickup!")
                        st.success(f"Order #{order_id} marked as Ready!")
                        st.rerun()
            else:
                st.info("No pending orders.")

elif choice == "Manage Menu":
    st.subheader("📖 Manage Menu")
    menu_df = load_menu()

    if not menu_df.empty:
        # Add a checkbox column for deletion
        menu_df["Delete"] = False
        edited = st.data_editor(menu_df, num_rows="dynamic")

        # Save updates (update/insert)
        if st.button("💾 Save Menu Updates"):
            upsert_menu(edited.drop(columns=["Delete"]))  # Keep only real columns
            st.success("✅ Menu updated successfully!")
            st.rerun()

        # Delete selected rows
        if st.button("🗑️ Delete Selected Rows"):
            to_delete = edited[edited["Delete"] == True]
            if not to_delete.empty:
                delete_menu_items(to_delete["ITEM"].tolist())
                st.success(f"🗑️ Deleted {len(to_delete)} item(s) successfully!")
                st.rerun()
            else:
                st.info("No rows selected for deletion.")
    else:
        st.info("No menu items available.")

elif choice == "AI Assistant":
    st.subheader("🤖 AI Assistant")
    q = st.text_area("Ask AI something:", key="staff_ai_q")
    if st.button("Ask AI", key="ask_ai_staff"):
        st.write(run_ai(q))

elif choice == "Feedback Review":
    st.subheader("📢 Feedback Review")
    fb = load_feedbacks_df()
    st.dataframe(fb, use_container_width=True) if not fb.empty else st.info("No feedbacks yet.")

elif choice == "Sales Report":
    st.subheader("💰 Sales Report")
    receipts = load_receipts_df()
    local = st.session_state.get("_local_receipts", [])
    if local:
        receipts = pd.concat([receipts, pd.DataFrame(local)], ignore_index=True)
    if receipts.empty:
        st.info("No sales yet.")
    else:
        all_items = []
        for _, row in receipts.iterrows():
            try:
                for it in _normalize_items_for_receipt(row.get("items", [])):
                    all_items.append({
                        "CATEGORY": it.get("category", "Uncategorized"),
                        "ITEM_NAME": it.get("name", "Unknown"),
                        "QUANTITY": int(it.get("qty", 1))
                    })
            except Exception:
                pass
        if all_items:
            sales_summary = pd.DataFrame(all_items).groupby(["CATEGORY", "ITEM_NAME"], as_index=False).sum()
            for cat in sales_summary["CATEGORY"].unique():
                st.markdown(f"### {cat} Sales Breakdown")
                cat_data = sales_summary[sales_summary["CATEGORY"] == cat]
                fig, ax = plt.subplots(figsize=(4, 4))
                ax.pie(cat_data["QUANTITY"], labels=cat_data["ITEM_NAME"], autopct="%1.1f%%", startangle=90)
                ax.axis("equal")
                st.pyplot(fig)

    # ---------------------------
    # NON-STAFF / GUEST PORTAL
    # ---------------------------
    else:
        if "cart" not in st.session_state:
            st.session_state.cart = {}
        if "notifications" not in st.session_state:
            st.session_state.notifications = []

        menu_df = load_menu()
        left_col, right_col = st.columns([1.3, 1])

        with left_col:
            st.markdown("### 🤖 BiteHub Assistant")
            with st.expander("💬 Ask BiteHub AI", expanded=False):
                q = st.text_input("Ask something:", key="user_ai_q", placeholder="e.g. What’s the best seller?")
                if st.button("Ask AI", key="ask_ai_user"):
                    # Load menu from DB
                    menu_df = load_menu()
                    menu_list = "\n".join([f"{row['CATEGORY']} - {row['ITEM']} (₱{row['PRICE']})" for _, row in menu_df.iterrows()])

                    # Construct prompt
                    prompt = f"""
                    You are BiteHub's AI assistant. Only use items from the menu below.
                    Prices are in Pesos (₱). Do NOT invent items or prices.

                    MENU:
                    {menu_list}

                    USER QUESTION: {q}
                    """

                    # Run AI with context
                    st.write(run_ai(prompt))

            st.markdown("### 📖 Menu & Ordering")
            if menu_df is None or menu_df.empty:
                st.warning("⚠️ Menu is empty.")
            else:
                cat_col, item_col, price_col = detect_menu_columns(menu_df)
                for cat in menu_df[cat_col].dropna().unique():
                    st.markdown(f"#### 🍽️ {cat}")
                    for _, row in menu_df[menu_df[cat_col] == cat].iterrows():
                        name = row[item_col]
                        price = float(row[price_col])
                        c1, c2, c3 = st.columns([3, 1, 1])
                        c1.write(name)
                        c2.write(f"₱{price:.2f}")
                        if c3.button("➕ Add", key=f"add_{name}"):
                            if name not in st.session_state.cart:
                                st.session_state.cart[name] = {"qty": 1, "price": price}
                            else:
                                st.session_state.cart[name]["qty"] += 1
                            st.success(f"{name} added to cart!")

            # 🛒 CART SECTION — always visible
            st.markdown("### 🛒 Your Cart")
            cart = st.session_state.cart
            if not cart:
                st.info("Your cart is empty.")
            else:
                total = 0
                for item, details in list(cart.items()):
                    qty, price = details["qty"], details["price"]
                    subtotal = qty * price
                    total += subtotal
                    c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
                    c1.markdown(f"**{item}**")
                    c2.write(f"₱{price:.2f}")
                    if c3.button("➕", key=f"inc_{item}"):
                        cart[item]["qty"] += 1
                        st.rerun()
                    if c3.button("➖", key=f"dec_{item}"):
                        if cart[item]["qty"] > 1:
                            cart[item]["qty"] -= 1
                        else:
                            del cart[item]
                        st.rerun()
                    if c4.button("🗑 Remove", key=f"rm_{item}"):
                        del cart[item]
                        st.rerun()
                    st.write(f"Qty: {qty} | Subtotal: ₱{subtotal:.2f}")

                st.markdown(f"### 💵 Total: ₱{total:.2f}")
                if st.button("💳 Proceed to Payment"):
                    st.session_state.page = "payment"
                    st.session_state.cart_total = total
                    st.rerun()

        # RIGHT SIDE — Feedbacks, Notifications, Order History
        with right_col:
            st.subheader("⭐ Feedbacks")
            if not is_guest:
                with st.form("feedback_form"):
                    item = st.selectbox("Item", menu_df["ITEM"].unique())
                    fb = st.text_area("Your feedback")
                    rt = st.slider("Rating", 1, 5, 3)
                    if st.form_submit_button("Submit"):
                        save_feedback(item, fb, rt, user["username"])
                        st.success("Feedback submitted!")
            else:
                st.warning("Guests cannot submit feedbacks.")

            st.divider()
            st.subheader("📢 Notifications")
            notes = get_notifications_for_user(user["username"])
            if notes:
                for n in notes:
                    st.info(n)
            else:
                st.info("No notifications yet.")
            if st.button("Clear"):
                clear_notifications_for_user(user["username"])
                st.success("Cleared.")

            st.divider()
            st.subheader("📜 Order History")
            hist = load_receipts_df()
            if not hist.empty:
                u_orders = hist[hist["user_id"] == user["username"]]
                if not u_orders.empty:
                    st.dataframe(u_orders.sort_values(by="timestamp", ascending=False))
                else:
                    st.info("No orders yet.")
            else:
                st.info("No receipts found.")

        st.divider()
        if st.button("🚪 Log Out"):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.session_state.page = "login"
            st.rerun()

# ---------- PAYMENT PAGE ----------
elif st.session_state.page == "payment":
    user = st.session_state.user or {"username": "Guest", "role": "Guest"}
    pending_cart = st.session_state.get("cart", {})

    if not pending_cart:
        st.warning("No pending order found. Go back to your cart.")
        if st.button("⬅️ Back to Main"):
            st.session_state.page = "main"
            st.rerun()
    else:
        total_cost = sum(v.get("qty", 1) * v.get("price", 0.0) for v in pending_cart.values())
        st.subheader("💳 Payment Confirmation")
        st.write(f"### 💵 Total Amount: ₱{total_cost:.2f}")

        method = st.radio("Select Payment Method", ["Cash", "GCash (Scan QR)", "Card"], key="pay_method")
        pickup_dt = st.text_input(
            "Pickup Time (YYYY-MM-DD HH:MM)",
            value=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )

        # always save as Pending so staff marks Ready manually
        if method == "Cash":
            if st.button("Confirm Cash Payment"):
                order_id = f"ORD-{random.randint(100000,999999)}"
                save_receipt(order_id, pending_cart, total_cost, "Cash",
                             user.get("username", "Guest"), pickup_dt, status="Pending")
                st.success("✅ Order recorded (Pending). Staff will mark it Ready when prepared.")
                st.session_state.cart.clear()
                st.session_state.page = "main"
                st.rerun()

        elif method == "GCash (Scan QR)":
            st.info("📱 Please scan the QR code below using your GCash app to pay.")
            st.image("Qr.jpg", caption="Scan this QR to pay via GCash", width=250)
            st.markdown(f"**Amount to pay:** ₱{total_cost:.2f}")

            if st.button("✅ I've Paid via GCash"):
                order_id = f"ORD-{random.randint(100000,999999)}"
                save_receipt(order_id, pending_cart, total_cost, "GCash",
                             user.get("username", "Guest"), pickup_dt, status="Pending")
                st.success("✅ Order recorded (Pending). Staff will mark it Ready when prepared.")
                st.session_state.cart.clear()
                st.session_state.page = "main"
                st.rerun()

        elif method == "Card":
            st.text_input("Card Number", key="card_num")
            st.text_input("Expiry (MM/YY)", key="card_exp")
            st.text_input("CVV", key="card_cvv")
            if st.button("Simulate Card Payment Success"):
                order_id = f"ORD-{random.randint(100000,999999)}"
                save_receipt(order_id, pending_cart, total_cost, "Card",
                             user.get("username", "Guest"), pickup_dt, status="Pending")
                st.success("✅ Order recorded (Pending). Staff will mark it Ready when prepared.")
                st.session_state.cart.clear()
                st.session_state.page = "main"
                st.rerun()
