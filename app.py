
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
# LOGIN + SIGNUP + MAIN PORTAL + PAYMENT
# ---------------------------

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
            create_account(new_username, new_password, role="User")
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

        if choice == "Dashboard":
            st.subheader("📊 Staff Dashboard")
            st.info("Metrics and KPIs coming soon.")

        elif choice == "Pending Orders":
            st.subheader("📦 Pending Orders")
            receipts = load_receipts_df()
            pending_orders = receipts[receipts["status"] == "Pending"] if not receipts.empty else pd.DataFrame()
            if not pending_orders.empty:
                for _, row in pending_orders.iterrows():
                    order_id = row.get("order_id") or row.get("orderId") or row.get("id")
                    st.markdown(f"**Order ID:** {order_id} | **User:** {row.get('user_id')} | **Payment:** {row.get('payment_method')}")
                    st.write("Items:", row.get("items"))
                    if st.button(f"✅ Mark Ready ({order_id})"):
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
                edited = st.data_editor(menu_df, num_rows="dynamic")
                if st.button("💾 Save Menu Updates"):
                    upsert_menu(edited)
                    st.success("✅ Menu updated successfully!")
                    st.rerun()
            else:
                st.info("No menu items available.")

        elif choice == "AI Assistant":
            st.subheader("🤖 AI Assistant")
            q = st.text_area("Ask AI something:", key="staff_ai_q")
            if st.button("Ask AI"):
                st.write(run_ai(q))

        elif choice == "Feedback Review":
            st.subheader("📢 Feedback Review")
            fb = load_feedbacks_df()
            if not fb.empty:
                st.dataframe(fb, use_container_width=True)
            else:
                st.info("No feedbacks yet.")

        elif choice == "Sales Report":
            st.subheader("💰 Sales Report")
            receipts = load_receipts_df()
            local = st.session_state.get("_local_receipts", [])
            if local:
                local_df = pd.DataFrame(local)
                receipts = pd.concat([receipts, local_df], ignore_index=True)
            if receipts.empty:
                st.info("No sales yet.")
            else:
                all_items = []
                for _, row in receipts.iterrows():
                    try:
                        items = json.loads(row.get("items", "[]"))
                        for it in items:
                            cat = it.get("category", "Uncategorized")
                            name = it.get("name", "Unknown")
                            qty = int(it.get("qty", 1))
                            all_items.append({"CATEGORY": cat, "ITEM_NAME": name, "QUANTITY": qty})
                    except Exception:
                        continue
                if all_items:
                    sales = pd.DataFrame(all_items).groupby(["CATEGORY", "ITEM_NAME"], as_index=False).sum()
                    for cat in sales["CATEGORY"].unique():
                        st.markdown(f"### {cat} Sales Breakdown")
                        cat_data = sales[sales["CATEGORY"] == cat]
                        fig, ax = plt.subplots(figsize=(3, 3))
                        ax.pie(cat_data["QUANTITY"], labels=cat_data["ITEM_NAME"], autopct="%1.1f%%", startangle=90)
                        ax.axis("equal")
                        st.pyplot(fig)
                else:
                    st.info("No sales data yet.")

    # ---------------------------
    # NON-STAFF / GUEST PORTAL
    # ---------------------------
    else:
        if "cart" not in st.session_state:
            st.session_state.cart = {}
        if "notifications" not in st.session_state:
            st.session_state.notifications = []

        menu_df = load_menu()
        left_col, right_col = st.columns([1.2, 1])

        # ---- LEFT COLUMN: AI + MENU + CART ----
        with left_col:
            # 🤖 AI Assistant
            st.markdown("### 🤖 BiteHub Assistant")
            with st.expander("💬 Ask BiteHub AI", expanded=False):
                user_question = st.text_input("Ask about our menu:", key="user_ai_q")
                if st.button("Ask AI", key="ask_ai_user"):
                    if user_question.strip():
                        st.write(run_ai(user_question))
                    else:
                        st.warning("Please enter a question.")

            # 📖 MENU
            st.markdown("### 📖 Menu & Ordering")
            if menu_df is None or menu_df.empty:
                st.warning("⚠️ Menu is currently empty.")
            else:
                detected_cat, detected_item, detected_price = detect_menu_columns(menu_df)
                if detected_item and detected_price:
                    if not detected_cat:
                        menu_df["_SINGLE_CAT"] = "Menu"
                        detected_cat = "_SINGLE_CAT"

                    for cat in menu_df[detected_cat].fillna("Uncategorized").unique():
                        st.markdown(f"#### 🍽️ {cat}")
                        for _, row in menu_df[menu_df[detected_cat] == cat].iterrows():
                            item = row[detected_item]
                            price = float(row[detected_price])
                            c1, c2, c3 = st.columns([3, 1, 1])
                            with c1:
                                st.markdown(f"**{item}**")
                            with c2:
                                st.markdown(f"₱{price:.2f}")
                            with c3:
                                if st.button("➕ Add", key=f"add_{cat}_{item}"):
                                    if item not in st.session_state.cart:
                                        st.session_state.cart[item] = {"qty": 1, "price": price}
                                    else:
                                        st.session_state.cart[item]["qty"] += 1
                                    st.success(f"Added {item} to cart!")

            # 🛒 CART SECTION
            st.markdown("### 🛒 Your Cart")
            cart = st.session_state.get("cart", {})
            if not cart:
                st.info("Your cart is empty.")
            else:
                total = 0
                for name, info in cart.items():
                    qty, price = info["qty"], info["price"]
                    subtotal = qty * price
                    total += subtotal
                    st.write(f"- {name} x {qty} = ₱{subtotal:.2f}")
                st.markdown(f"**Total: ₱{total:.2f}**")
                if st.button("💳 Proceed to Payment"):
                    st.session_state.page = "payment"
                    st.rerun()

        # ---- RIGHT COLUMN: FEEDBACKS + NOTIFICATIONS ----
        with right_col:
            st.subheader("⭐ Feedbacks & Sentiment")
            if not is_guest:
                if not menu_df.empty:
                    det_cat, det_item, det_price = detect_menu_columns(menu_df)
                    items = menu_df[det_item].fillna("Unknown").tolist()
                    with st.form("feedback_form"):
                        chosen_item = st.selectbox("Item:", items)
                        fb_text = st.text_area("Feedback:")
                        rating = st.slider("Rating (1-5)", 1, 5, 3)
                        if st.form_submit_button("Submit"):
                            if fb_text:
                                save_feedback(chosen_item, fb_text, rating, user["username"])
                                st.success("✅ Feedback submitted!")
                            else:
                                st.warning("Please enter feedback.")
                else:
                    st.info("No menu available.")
            else:
                st.warning("Guests cannot submit feedback.")

            st.divider()
            st.subheader("📢 Notifications")
            notes = get_notifications_for_user(user.get("username"))
            if notes:
                for note in notes:
                    st.info(note)
            else:
                st.info("No notifications.")

            if st.button("🧹 Clear Notifications"):
                st.session_state.notifications = []
                st.rerun()

            st.divider()
            st.subheader("📜 Order History")
            if not is_guest:
                history = load_receipts_df()
                if not history.empty and "user_id" in history.columns:
                    my_orders = history[history["user_id"] == user["username"]]
                    if not my_orders.empty:
                        st.dataframe(my_orders.sort_values("timestamp", ascending=False), use_container_width=True)
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

# ---------------------------
# PAYMENT PAGE
# ---------------------------
elif st.session_state.page == "payment":
    user = st.session_state.user or {"username": "Guest", "role": "Guest"}
    cart = st.session_state.get("cart", {})
    if not cart:
        st.warning("Cart is empty.")
        if st.button("⬅️ Back"):
            st.session_state.page = "main"
            st.rerun()
    else:
        total = sum(v["qty"] * v["price"] for v in cart.values())
        st.subheader("💳 Payment Confirmation")
        st.write(f"### Total: ₱{total:.2f}")

        method = st.radio("Select Payment Method", ["Cash", "GCash (QR)", "Card"])
        pickup_time = st.text_input("Pickup Time (YYYY-MM-DD HH:MM)", datetime.now().strftime("%Y-%m-%d %H:%M"))

        # ---- GCash ----
        if method == "GCash (QR)":
            st.image("Qr.jpg", caption="📱 Scan to pay via GCash", width=250)
            st.info(f"Pay ₱{total:.2f} and then confirm below.")

        # ---- Card ----
        elif method == "Card":
            st.markdown("### 💳 Enter Card Details Securely")
            card_col1, card_col2 = st.columns(2)
            with card_col1:
                card_name = st.text_input("Cardholder Name")
                card_number = st.text_input("Card Number", max_chars=19, placeholder="1234 5678 9012 3456")
            with card_col2:
                exp_date = st.text_input("Expiry Date (MM/YY)", max_chars=5)
                cvv = st.text_input("CVV", max_chars=3, type="password")

            if not card_name or not card_number or not exp_date or not cvv:
                st.warning("⚠️ Please complete all card details before confirming.")

        # ---- Confirm Button ----
        if st.button("✅ Confirm Order"):
            if method == "Card":
                # Validate card details before proceeding
                if not card_name or not card_number or not exp_date or not cvv:
                    st.error("❌ Please fill out all card fields.")
                    st.stop()
                elif len(card_number.replace(" ", "")) < 13 or len(card_number.replace(" ", "")) > 19:
                    st.error("❌ Invalid card number.")
                    st.stop()
                elif not re.match(r"^(0[1-9]|1[0-2])\/\d{2}$", exp_date):
                    st.error("❌ Invalid expiry date format (MM/YY).")
                    st.stop()
                elif not cvv.isdigit() or len(cvv) != 3:
                    st.error("❌ Invalid CVV.")
                    st.stop()

            order_id = f"ORD-{random.randint(100000,999999)}"
            save_receipt(order_id, cart, total, method, user.get("username", "Guest"), pickup_time, status="Pending")

            st.success(f"✅ Order {order_id} placed successfully! Wait for staff to mark as Ready.")
            st.session_state.cart.clear()
            st.session_state.page = "main"
            st.rerun()
