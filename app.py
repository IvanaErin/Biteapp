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
def save_receipt(order_id, items, total, payment_method, user_id, pickup_dt, status):
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
        if category_col is None and ("CATEGORY" in cu or "CAT" == cu):
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
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": question + "\n" + extra_context}]
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
# PAGES: LOGIN, SIGNUP, MAIN, PAYMENT
# ---------------------------

# ---------- LOGIN PAGE ----------
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

# ---------- SIGNUP PAGE ----------
elif st.session_state.page == "signup":
    st.markdown("<h1 style='text-align: center; color: #FF6F61;'>📝 BiteHub — Sign Up</h1>", unsafe_allow_html=True)
    new_user = st.text_input("New Username", placeholder="Enter username", key="signup_username")
    new_pass = st.text_input("New Password", placeholder="Enter password", type="password", key="signup_password")
    confirm_pass = st.text_input("Confirm Password", placeholder="Re-enter password", type="password", key="signup_confirm")

    if st.button("Create Account ✅", use_container_width=True):
        if not new_user or not new_pass:
            st.error("⚠️ Username and password required.")
        elif new_pass != confirm_pass:
            st.error("❌ Passwords do not match.")
        elif get_account(new_user):
            st.error("🚫 Username already exists.")
        else:
            hashed = hash_password(new_pass)
            save_account(new_user, hashed, "Non-Staff")
            st.success("🎉 Account created successfully! Please log in below.")
            st.session_state.page = "login"
            st.rerun()

    st.markdown("---")
    if st.button("⬅️ Back to Login", use_container_width=True):
        st.session_state.page = "login"
        st.rerun()

# ---------- MAIN PORTAL (Staff / Non-Staff / Guest) ----------
elif st.session_state.page == "main":
    # ensure user exists
    if "user" not in st.session_state or not st.session_state.user:
        st.session_state.user = {"username": "Guest", "role": "Guest", "loyalty_points": 0}

    user = st.session_state.user
    role = user.get("role", "Guest")
    is_guest = (role == "Guest")

    st.title(f"🏫 Welcome {user['username']} to BiteHub")

    # ---------- STAFF PORTAL ----------
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
                st.dataframe(pending_orders, use_container_width=True)
            else:
                st.info("No pending orders.")

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

        elif choice == "AI Assistant":
            st.subheader("🤖 AI Assistant")
            q = st.text_area("Ask AI something:", key="staff_ai_q")
            if st.button("Ask AI", key="ask_ai_staff"):
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
            if not receipts.empty:
                st.dataframe(receipts, use_container_width=True)
            else:
                st.info("No sales yet.")

    # ---------- NON-STAFF & GUEST PORTAL ----------
    else:
        # ensure session defaults for this portal
        if "cart" not in st.session_state:
            st.session_state.cart = {}
        if "notifications" not in st.session_state:
            st.session_state.notifications = []

        menu_df = load_menu()
        left_col, right_col = st.columns([1.2, 1])

        # --- LEFT: AI, MENU & ORDERING ---
        with left_col:
            # AI
            st.subheader("🤖 AI Assistant")
            q = st.text_area("Ask AI something:", key="user_ai_q")
            if st.button("Ask AI", key="ask_ai_user"):
                st.write(run_ai(q))

            # ---------- MENU & ORDERING ----------
            st.markdown("### 📖 Menu & Ordering")

            # detect columns for safety
            if menu_df is None or menu_df.empty:
                st.warning("⚠️ Menu is currently empty.")
                detected_category_col = detected_item_col = detected_price_col = None
            else:
                detected_category_col, detected_item_col, detected_price_col = detect_menu_columns(menu_df)
                # If detection failed, show columns to help debugging
                if not detected_item_col or not detected_price_col:
                    st.write("Menu columns:", menu_df.columns.tolist())
                    st.error("Menu format not recognized. Missing item or price column.")
                    detected_category_col = detected_item_col = detected_price_col = None

            # Render menu only if detection succeeded
            if detected_item_col and detected_price_col:
                # Optional: if no category col detected, create a fake single category
                if not detected_category_col:
                    menu_df["_SINGLE_CAT"] = "Menu"
                    detected_category_col = "_SINGLE_CAT"

                categories = menu_df[detected_category_col].fillna("Uncategorized").unique()
                for cat in categories:
                    st.markdown(f"#### 🍽️ {cat}")
                    cat_items = menu_df[menu_df[detected_category_col] == cat]
                    for _, row in cat_items.iterrows():
                        # Safely obtain values using .get with fallback
                        item_name = row.get(detected_item_col, str(row[detected_item_col]) if detected_item_col in row.index else "Unknown Item")
                        price_val = row.get(detected_price_col, 0.0)
                        # coerce price to float safely
                        try:
                            price_val = float(price_val)
                        except Exception:
                            price_val = 0.0

                        col1, col2, col3 = st.columns([3, 1, 1])
                        with col1:
                            st.markdown(
                                f"<div style='font-size:16px; font-weight:500;'>{item_name}</div>",
                                unsafe_allow_html=True
                            )
                        with col2:
                            st.markdown(
                                f"<div style='font-size:15px; color:#FFD700;'>₱{price_val:.2f}</div>",
                                unsafe_allow_html=True
                            )
                        with col3:
                            btn_key = f"add_{cat}_{item_name}"
                            if st.button("➕ Add", key=btn_key, use_container_width=True):
                                # Add to cart (dict style)
                                if item_name not in st.session_state.cart:
                                    st.session_state.cart[item_name] = {"qty": 1, "price": price_val}
                                else:
                                    st.session_state.cart[item_name]["qty"] += 1
                                st.success(f"✅ {item_name} added to cart!")

            # CART DISPLAY
            if st.session_state.cart:
                st.divider()
                st.subheader("🛒 Your Cart")

                cart_data = []
                total_price = 0.0
                for item, details in st.session_state.cart.items():
                    qty = details.get("qty", 1)
                    price = details.get("price", 0.0)
                    try:
                        subtotal = qty * float(price)
                    except Exception:
                        subtotal = qty * 0.0
                    total_price += subtotal
                    cart_data.append({
                        "Item": item,
                        "Quantity": qty,
                        "Price": f"₱{float(price):.2f}",
                        "Subtotal": f"₱{subtotal:.2f}"
                    })

                st.dataframe(pd.DataFrame(cart_data), use_container_width=True)
                st.markdown(f"### 💵 Total: ₱{total_price:.2f}")

                colX, colY = st.columns([1, 1])
                with colX:
                    if st.button("🧾 Checkout"):
                        # send to payment page (do not clear cart here)
                        st.session_state.page = "payment"
                        st.rerun()
                with colY:
                    if st.button("❌ Clear Cart"):
                        st.session_state.cart.clear()
                        st.info("Cart cleared.")
            else:
                st.info("Your cart is empty.")

        # --- RIGHT: SENTIMENT, FEEDBACKS, NOTIFICATIONS, HISTORY ---
        with right_col:
            # Sentiment & Feedbacks
            st.subheader("⭐ Feedbacks & Sentiment")
            if not is_guest:
                if not menu_df.empty:
                    # Use detected_item_col if available; else fallback
                    available_items = []
                    if menu_df is not None and not menu_df.empty:
                        det_cat, det_item, det_price = detect_menu_columns(menu_df)
                        if det_item:
                            available_items = menu_df[det_item].fillna("Unknown").tolist()
                        else:
                            available_items = menu_df.iloc[:, 0].fillna("Unknown").tolist()
                    else:
                        available_items = []

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

            # Notifications
            st.divider()
            st.subheader("📢 Notifications")
            if st.session_state.notifications:
                for i, note in enumerate(st.session_state.notifications):
                    st.info(note, key=f"notif_{i}")
            else:
                st.info("No notifications.")
            if st.button("Clear notifications", key="clear_notifs"):
                st.session_state.notifications.clear()

            # Order History
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

        # LOGOUT BUTTON
        st.divider()
        if st.button("🚪 Log Out"):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.session_state.page = "login"
            st.rerun()

# ---------- PAYMENT PAGE ----------
elif st.session_state.page == "payment":
    import qrcode
    from io import BytesIO
    import base64

    user = st.session_state.user or {"username": "Guest", "role": "Guest"}
    pending_cart = st.session_state.get("cart", {})

    # no cart detected
    if not pending_cart:
        st.warning("No pending order found. Go back to your cart.")
        if st.button("⬅️ Back to Main"):
            st.session_state.page = "main"
            st.rerun()
    else:
        # compute total
        total_cost = sum(v.get("qty", 1) * v.get("price", 0.0) for v in pending_cart.values())
        st.subheader("💳 Payment Confirmation")
        st.write(f"### Total: ₱{total_cost:.2f}")

        # pickup input
        pickup_dt = st.text_input(
            "Pickup Time (YYYY-MM-DD HH:MM)",
            value=datetime.now().strftime("%Y-%m-%d %H:%M")
        )

        # select method
        method = st.radio("Select Payment Method", ["Cash", "GCash", "Card"], key="pay_method")

        # order id generator
        order_id = f"ORD-{random.randint(100000, 999999)}"

        # --- CASH PAYMENT ---
        if method == "Cash":
            st.info("💵 Please prepare the exact amount for cash payment upon pickup.")
            if st.button("Confirm Cash Payment"):
                save_receipt(order_id, pending_cart, total_cost, "Cash",
                             user.get("username", "Guest"), pickup_dt, "Completed")
                st.success("✅ Order confirmed! (Cash)")
                st.session_state.cart.clear()
                st.session_state.page = "main"
                st.rerun()

        # --- GCASH PAYMENT ---
        elif method == "GCash":
            gcash_number = "09628528940"  # <-- change to your real GCash number

            # Generate simulated dynamic QR
            qr_data = f"GCASH PAYMENT\nOrder: {order_id}\nNumber: {gcash_number}\nAmount: ₱{total_cost:.2f}"
            qr_img = qrcode.make(qr_data)
            buf = BytesIO()
            qr_img.save(buf, format="PNG")
            qr_base64 = base64.b64encode(buf.getvalue()).decode("utf-8")

            st.markdown("#### 📱 Scan to Pay via GCash")
            st.image(f"data:image/png;base64,{qr_base64}", width=250)

            # clickable GCash Pay link (works on mobile)
            pay_link = f"https://link.gcash.com/pay?amount={total_cost:.2f}&name=BiteHub&mobile={gcash_number}"
            st.markdown(
                f"<a href='{pay_link}' target='_blank' "
                f"style='text-decoration:none; background-color:#00BFA5; color:white; "
                f"padding:10px 16px; border-radius:8px; font-weight:600;'>💰 Pay ₱{total_cost:.2f} via GCash</a>",
                unsafe_allow_html=True
            )

            st.caption("After paying, click below to confirm your payment.")

            if st.button("✅ Simulate GCash Payment Success"):
                save_receipt(order_id, pending_cart, total_cost, "GCash",
                             user.get("username", "Guest"), pickup_dt, "Completed")
                st.success("✅ GCash Payment Successful!")
                st.session_state.cart.clear()
                st.session_state.page = "main"
                st.rerun()

        # --- CARD PAYMENT ---
        elif method == "Card":
            st.text_input("Card Number", key="card_num")
            st.text_input("Expiry MM/YY", key="card_exp")
            st.text_input("CVV", key="card_cvv")
            if st.button("Simulate Card Payment Success"):
                save_receipt(order_id, pending_cart, total_cost, "Card",
                             user.get("username", "Guest"), pickup_dt, "Completed")
                st.success("✅ Card Payment Successful!")
                st.session_state.cart.clear()
                st.session_state.page = "main"
                st.rerun()

        # back option
        st.divider()
        if st.button("⬅️ Back to Cart"):
            st.session_state.page = "main"
            st.rerun()
