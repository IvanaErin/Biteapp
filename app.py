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

# ---------------------------
# RECEIPTS
# ---------------------------
def save_receipt(order_id, items, total, payment_method, user_id, pickup_dt, status="Pending"):
    """
    Save a receipt. `items` expected to be a serializable structure (e.g. dict or list).
    Default status is 'Pending' so staff can mark Ready later.
    """
    items_json = json.dumps(items)
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
# NOTIFICATIONS (session-based; optional DB insert)
# ---------------------------
def add_notification(user_id: str, message: str):
    """
    Add a notification to session state. If DB connected and a notifications table is available,
    this function attempts to insert to DB but silently ignores DB errors.
    """
    _ensure_local_db()
    # Add to session notifications (global for now)
    # You may want per-user notifications dict; for simplicity, append a message with user id prefix
    note = f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {message}"
    # If user is current user, show immediately; otherwise store in a list with user id
    st.session_state.notifications.append({"user_id": user_id, "message": note})

    # Optional DB insert (best-effort, ignore errors)
    conn = get_connection()
    if not conn:
        return
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO notifications (user_id, message, timestamp) VALUES (%s, %s, %s)",
                (user_id, message, datetime.now())
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
    Return list of messages for the given user from session state (and DB optionally).
    """
    _ensure_local_db()
    msgs = []
    for n in st.session_state.notifications:
        # n is dict {"user_id":..., "message":...}
        if n.get("user_id") == user_id:
            msgs.append(n.get("message"))
    # Note: DB-backed retrieval can be added later.
    return msgs

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
    # return DataFrame with at least CATEGORY, ITEM, PRICE (column names preserved)
    conn = get_snowflake_conn()
    # if no connection, return a fallback DataFrame
    if not conn:
        default_menu = {
            "CATEGORY": ["Breakfast","Breakfast","Lunch","Lunch","Drinks","Drinks","Snacks","Snacks"],
            "ITEM": ["Pancakes","Omelette","Burger","Pizza","Coffee","Juice","Chips","Donut"],
            "PRICE": [50,40,80,120,30,40,20,25]
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
# LOGIN + SIGNUP + MAIN PORTAL + PAYMENT
# ---------------------------
# Default page already set above

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
                st.session_state.user = acc
                st.session_state.page = "main"
                st.success(f"✅ Welcome {acc['username']}!")
                st.rerun()
            else:
                st.error("❌ Invalid username or password.")

    with col3:
        if st.button("🎟️ Guest Account", use_container_width=True):
            st.session_state.user = {"username": "Guest", "role": "Guest", "loyalty_points": 0}
            st.session_state.page = "main"
            st.rerun()

    with col4:
        if st.button("📝 Create Account", use_container_width=True):
            st.session_state.page = "signup"
            st.rerun()

# ---------- MAIN PORTAL (Staff / Non-Staff / Guest) ----------
elif st.session_state.page == "main":
    # Ensure user/role are properly loaded
    if "user" not in st.session_state or not st.session_state.user:
        st.session_state.page = "login"
        st.rerun()

    user = st.session_state.user
    role = user.get("role", "Guest")
    is_guest = (role == "Guest")

    # ---------------------------
    # STAFF PORTAL
    # ---------------------------
    if role == "Staff":
        if "staff_choice" not in st.session_state:
            st.session_state.staff_choice = "Dashboard"

        st.session_state.staff_choice = st.sidebar.radio(
            "Staff Menu",
            ["Dashboard", "Pending Orders", "Manage Menu", "AI Assistant", "Feedback Review", "Sales Report"],
            index=["Dashboard", "Pending Orders", "Manage Menu", "AI Assistant", "Feedback Review", "Sales Report"].index(
                st.session_state.staff_choice
            )
        )

        choice = st.session_state.staff_choice

        # ------------------- Dashboard -------------------
        if choice == "Dashboard":
            st.subheader("📊 Staff Dashboard")
            st.info("Metrics and KPIs coming soon.")

        # ------------------- Pending Orders -------------------
        elif choice == "Pending Orders":
            st.subheader("📦 Pending Orders")
            receipts = load_receipts_df()

            # Only pending orders
            pending_orders = receipts[receipts["status"] == "Pending"] if not receipts.empty else pd.DataFrame()

            if not pending_orders.empty:
                # Display each pending order with a "Mark as Ready" button
                for idx, row in pending_orders.iterrows():
                    # use order_id column name used across load/save
                    order_id = row.get("order_id") or row.get("orderId") or row.get("id")
                    st.markdown(f"**Order ID:** {order_id} | **User:** {row.get('user_id')} | **Payment:** {row.get('payment_method')}")
                    st.write("Items:", row.get("items"))

                    btn_key = f"ready_{order_id}"
                    if st.button("✅ Mark as Ready", key=btn_key):
                        # Update order status to Ready
                        update_order_status(order_id, "Ready")

                        # Add notification to the corresponding user
                        add_notification(row.get("user_id"), f"Your order #{order_id} is ready for pickup!")

                        st.success(f"Order #{order_id} marked as Ready!")
                        st.rerun()  # Refresh the page to update table
            else:
                st.info("No pending orders.")

        # ------------------- Manage Menu -------------------
        elif choice == "Manage Menu":
            st.subheader("📖 Manage Menu")
            menu_df = load_menu()
            if not menu_df.empty:
                edited = st.data_editor(menu_df, num_rows="dynamic")
                if st.button("Save Menu Updates"):
                    upsert_menu(edited)
                    st.success("✅ Menu updated successfully!")
                    st.rerun()
            else:
                st.info("No menu items available.")

        # ------------------- AI Assistant -------------------
        elif choice == "AI Assistant":
            st.subheader("🤖 AI Assistant")
            q = st.text_area("Ask AI something:", key="staff_ai_q")
            if st.button("Ask AI", key="ask_ai_staff"):
                st.write(run_ai(q))

        # ------------------- Feedback Review -------------------
        elif choice == "Feedback Review":
            st.subheader("📢 Feedback Review")
            fb = load_feedbacks_df()
            if not fb.empty:
                st.dataframe(fb, use_container_width=True)
            else:
                st.info("No feedbacks yet.")

        # ------------------- Sales Report -------------------
        elif choice == "Sales Report":
            st.subheader("💰 Sales Report")
            receipts = load_receipts_df()
            if receipts.empty:
                st.info("No sales yet.")
            else:
                # Extract items from JSON and aggregate
                all_items = []
                for _, row in receipts.iterrows():
                    items_json = row.get("items")
                    if not items_json:
                        continue
                    try:
                        items_list = json.loads(items_json)
                    except Exception:
                        continue
                    if not isinstance(items_list, list):
                        continue
                    for it in items_list:
                        if not isinstance(it, dict):
                            continue
                        name = it.get("name") or it.get("ITEM_NAME") or it.get("item") or "Unknown Item"
                        try:
                            qty = int(it.get("qty") or it.get("QUANTITY") or 1)
                        except Exception:
                            qty = 1
                        category = it.get("category") or "Uncategorized"
                        all_items.append({"CATEGORY": category, "ITEM_NAME": name, "QUANTITY": qty})

                if not all_items:
                    st.info("No sales items found in receipts.")
                else:
                    sales_summary = pd.DataFrame(all_items)
                    sales_summary = sales_summary.groupby(["CATEGORY", "ITEM_NAME"], as_index=False).sum()
                    categories = sales_summary["CATEGORY"].dropna().unique()

                    # Show pie chart per category
                    for cat in categories:
                        st.markdown(f"### {cat} Sales Breakdown")
                        cat_data = sales_summary[sales_summary["CATEGORY"] == cat]
                        if cat_data.empty:
                            st.info(f"No sales for {cat} yet.")
                            continue
                        # prepare values and labels
                        values = cat_data["QUANTITY"].tolist()
                        labels = cat_data["ITEM_NAME"].tolist()
                        fig, ax = plt.subplots()
                        ax.pie(values, labels=labels, autopct="%1.1f%%", startangle=90, wedgeprops={"edgecolor": "w"})
                        ax.axis("equal")
                        st.pyplot(fig)

    # ---------------------------
    # NON-STAFF / GUEST PORTAL
    # ---------------------------
    else:
        # --- Initialize session variables ---
        if "cart" not in st.session_state:
            st.session_state.cart = {}
        if "notifications" not in st.session_state:
            st.session_state.notifications = []

        menu_df = load_menu()
        left_col, right_col = st.columns([1.2, 1])

        # --- LEFT: AI, MENU & ORDERING ---
        with left_col:
            # 🤖 BiteHub Assistant (Compact)
            with st.container():
                st.markdown("### 🤖 BiteHub Assistant")
                with st.expander("💬 Ask BiteHub AI", expanded=False):
                    user_question = st.text_input(
                        "Ask about our meals, menu, or promos:",
                        key="user_ai_q",
                        placeholder="e.g. What’s today’s special?"
                    )

                    if st.button("Ask AI", key="ask_ai_user", use_container_width=False):
                        if user_question.strip():
                            # Build dynamic menu context
                            if menu_df is not None and not menu_df.empty:
                                cols = menu_df.columns.str.upper().tolist()
                                name_col = next((c for c in cols if "ITEM" in c or "NAME" in c or "PRODUCT" in c), None)
                                price_col = next((c for c in cols if "PRICE" in c), None)
                                cat_col = next((c for c in cols if "CAT" in c or "TYPE" in c), None)

                                if name_col:
                                    menu_lines = []
                                    # use original column names for values
                                    for _, row in menu_df.iterrows():
                                        name = str(row.get(name_col, "")).strip()
                                        price = f"₱{row.get(price_col, '')}" if price_col else ""
                                        cat = f"({row.get(cat_col, '')})" if cat_col else ""
                                        menu_lines.append(f"{name} {price} {cat}".strip())
                                    menu_text = "\n".join(menu_lines)
                                else:
                                    menu_text = "No valid item names found in menu."
                            else:
                                menu_text = "No menu data available."

                            system_prompt = (
                                "You are BiteHub’s friendly virtual canteen assistant. "
                                "Only talk about items listed below. "
                                "If a customer asks for something not in the list, politely say it's not available. "
                                "Be warm, conversational, and concise.\n\n"
                                f"--- MENU ---\n{menu_text}\n----------------\n"
                            )

                            try:
                                response = client.chat.completions.create(
                                    model="llama-3.1-8b-instant",
                                    messages=[
                                        {"role": "system", "content": system_prompt},
                                        {"role": "user", "content": user_question},
                                    ],
                                )
                                st.markdown(response.choices[0].message.content)
                            except Exception as e:
                                st.error(f"⚠️ AI Error: {e}")
                        else:
                            st.warning("Please enter a question first 😊")

# 📖 MENU & ORDERING
st.markdown("### 📖 Menu & Ordering")
if menu_df is None or menu_df.empty:
    st.warning("⚠️ Menu is currently empty.")
else:
    detected_category_col, detected_item_col, detected_price_col = detect_menu_columns(menu_df)
    if detected_item_col and detected_price_col:
        if not detected_category_col:
            menu_df["_SINGLE_CAT"] = "Menu"
            detected_category_col = "_SINGLE_CAT"

        categories = menu_df[detected_category_col].fillna("Uncategorized").unique()
        for cat in categories:
            st.markdown(f"#### 🍽️ {cat}")
            cat_items = menu_df[menu_df[detected_category_col] == cat]
            for _, row in cat_items.iterrows():
                item_name = row.get(detected_item_col, "Unknown Item")
                price_val = row.get(detected_price_col, 0.0)
                try:
                    price_val = float(price_val)
                except Exception:
                    price_val = 0.0

                col1, col2, col3 = st.columns([3, 1, 1])
                with col1:
                    st.markdown(
                        f"<div style='font-size:16px; font-weight:500;'>{item_name}</div>",
                        unsafe_allow_html=True,
                    )
                with col2:
                    st.markdown(
                        f"<div style='font-size:15px; color:#FFD700;'>₱{price_val:.2f}</div>",
                        unsafe_allow_html=True,
                    )
                with col3:
                    btn_key = f"add_{cat}_{item_name}"
                    if st.button("➕ Add", key=btn_key, use_container_width=True):
                        if item_name not in st.session_state.cart:
                            st.session_state.cart[item_name] = {"qty": 1, "price": price_val}
                        else:
                            st.session_state.cart[item_name]["qty"] += 1
                        st.success(f"✅ {item_name} added to cart!")

    # 🛒 CART & PAYMENT SECTION
    st.markdown("---")
    st.markdown("### 🛒 Your Cart")
    if st.session_state.cart:
        cart_items = []
        total_amount = 0
        for item, details in st.session_state.cart.items():
            qty = details["qty"]
            price = details["price"]
            subtotal = qty * price
            total_amount += subtotal
            cart_items.append([item, qty, f"₱{price:.2f}", f"₱{subtotal:.2f}"])

        cart_df = pd.DataFrame(cart_items, columns=["Item", "Qty", "Price", "Subtotal"])
        st.dataframe(cart_df, use_container_width=True, hide_index=True)
        st.markdown(f"### 💵 Total: ₱{total_amount:.2f}")

        payment_method = st.selectbox("Select Payment Method", ["Cash", "GCash", "Card"])
        pickup_dt = st.text_input("Pickup Date & Time (YYYY-MM-DD HH:MM)")

        if st.button("✅ Proceed to Payment"):
            order_id = secrets.token_hex(4)
            items = [{"name": item, "qty": d["qty"], "price": d["price"]} for item, d in st.session_state.cart.items()]
            save_receipt(order_id, items, total_amount, payment_method, st.session_state.user["username"], pickup_dt, "Pending")
            st.session_state.cart.clear()
            st.success("🎉 Order placed successfully! Please wait for staff to mark it Ready.")
    else:
        st.info("Your cart is empty.")
        # --- RIGHT: FEEDBACKS & NOTIFICATIONS ---
        with right_col:
            st.subheader("⭐ Feedbacks & Sentiment")
            if not is_guest:
                if not menu_df.empty:
                    det_cat, det_item, det_price = detect_menu_columns(menu_df)
                    available_items = menu_df[det_item].fillna("Unknown").tolist() if det_item else menu_df.iloc[:, 0].fillna("Unknown").tolist()
                    with st.form("feedback_form"):
                        item_choice = st.selectbox("Which item?", available_items, key="feedback_item")
                        feedback = st.text_area("Your feedback:", key="feedback_text")
                        rating = st.slider("Rate (1-5)", 1, 5, 3, key="feedback_rating")
                        submitted = st.form_submit_button("Submit Feedback")
                        if submitted:
                            if feedback:
                                save_feedback(item_choice, feedback, rating, user["username"])
                                st.success("✅ Feedback submitted!")
                            else:
                                st.warning("Feedback cannot be empty.")
                else:
                    st.info("Menu is empty. Feedback cannot be submitted.")
            else:
                st.warning("Guests cannot submit feedback. Please create an account.")

            st.divider()
            st.subheader("📢 Notifications")
            # show only notifications for this logged-in user
            notes = get_notifications_for_user(user.get("username"))
            if notes:
                for i, note in enumerate(notes):
                    st.info(note, key=f"notif_{i}")
            else:
                st.info("No notifications.")
            if st.button("Clear notifications", key="clear_notifs"):
                # clear only current user's notifications in session state
                st.session_state["notifications"] = [n for n in st.session_state.get("notifications", []) if n.get("user_id") != user.get("username")]
                st.success("✅ Notifications cleared")

            st.divider()
            st.subheader("📜 Order History")
            if not is_guest:
                history = load_receipts_df()
                if not history.empty and "user_id" in history.columns:
                    user_orders = history[history["user_id"] == user["username"]]
                    if not user_orders.empty:
                        st.dataframe(user_orders.sort_values(by="timestamp", ascending=False), use_container_width=True)
                    else:
                        st.info("No past orders yet.")
                else:
                    st.info("No past orders yet.")
            else:
                st.warning("Guests cannot view order history.")

        st.divider()
        if st.button("🚪 Log Out"):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
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

        st.divider()
        if st.button("⬅️ Back to Cart"):
            st.session_state.page = "main"
            st.rerun()
