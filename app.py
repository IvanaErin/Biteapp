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
    return snowflake.connector.connect(
        user=st.secrets["SNOWFLAKE_USER"],
        password=st.secrets["SNOWFLAKE_PASSWORD"],
        account=st.secrets["SNOWFLAKE_ACCOUNT"],
        warehouse=st.secrets["SNOWFLAKE_WAREHOUSE"],
        database=st.secrets["SNOWFLAKE_DATABASE"],
        schema=st.secrets["SNOWFLAKE_SCHEMA"]
    )

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
            columns=["order_id","items","total","payment_method","user_id","pickup_dt","status","timestamp"]
        )
    try:
        cur = conn.cursor()
        cur.execute("""
SELECT order_id, items, total, payment_method, user_id, pickup_time AS pickup_dt, status, timestamp
FROM receipts
ORDER BY timestamp DESC
""")
        rows = cur.fetchall()
        return pd.DataFrame(rows, columns=["order_id","items","total","payment_method","user_id","pickup_dt","status","timestamp"])
    finally:
        cur.close()
        conn.close()

# ---------------------------
# FEEDBACK
# ---------------------------
def save_feedback(item: str, feedback: str, rating: int, user_id: int):
    conn = get_connection()
    if not conn:
        _ensure_local_db()
        st.session_state._local_feedbacks.append({
            "item": item, "feedback": feedback, "rating": rating, "user_id": user_id
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
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["item","feedback","rating","user_id"])
    try:
        cur = conn.cursor()
        cur.execute("SELECT item, feedback, rating, user_id, timestamp FROM feedbacks ORDER BY timestamp DESC")
        rows = cur.fetchall()
        return pd.DataFrame(rows, columns=["item","feedback","rating","user_id","timestamp"])
    finally:
        cur.close()
        conn.close()

# ---------------------------
# MENU
# ---------------------------
def load_menu():
    conn = get_snowflake_conn()
    try:
        df = pd.read_sql(
            "SELECT CATEGORY, ITEM, PRICE FROM MENU ORDER BY CATEGORY, ITEM",
            conn
        )
        if df.empty:
            default_menu = {
                "Breakfast": {"Pancakes": 50, "Omelette": 40},
                "Lunch": {"Burger": 80, "Pizza": 120},
                "Drinks": {"Coffee": 30, "Juice": 40},
                "Snacks": {"Chips": 20, "Donut": 25}
            }
            cursor = conn.cursor()
            for cat, items in default_menu.items():
                for item, price in items.items():
                    cursor.execute(
                        "INSERT INTO MENU (CATEGORY, ITEM, PRICE) VALUES (%s, %s, %s)",
                        (cat, item, price)
                    )
            conn.commit()
            df = pd.read_sql(
                "SELECT CATEGORY, ITEM, PRICE FROM MENU ORDER BY CATEGORY, ITEM",
                conn
            )
        return df
    finally:
        conn.close()

def upsert_menu(df: pd.DataFrame):
    conn = get_snowflake_conn()
    try:
        cur = conn.cursor()
        for _, row in df.iterrows():
            cur.execute("""
                MERGE INTO MENU AS target
                USING (SELECT %s AS CATEGORY, %s AS ITEM, %s AS PRICE) AS source
                ON target.CATEGORY = source.CATEGORY AND target.ITEM = source.ITEM
                WHEN MATCHED THEN
                    UPDATE SET PRICE = source.PRICE
                WHEN NOT MATCHED THEN
                    INSERT (CATEGORY, ITEM, PRICE) VALUES (source.CATEGORY, source.ITEM, source.PRICE)
            """, (row["CATEGORY"], row["ITEM"], row["PRICE"]))
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
    st.session_state.cart = {}
if "notifications" not in st.session_state:
    st.session_state.notifications = []

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

if "page" not in st.session_state:
    st.session_state.page = "login"

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
    new_user = st.text_input("New Username", placeholder="Enter username")
    new_pass = st.text_input("New Password", placeholder="Enter password", type="password")
    confirm_pass = st.text_input("Confirm Password", placeholder="Re-enter password", type="password")

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


# ---------------------------
# MAIN PORTAL (Staff / Non-Staff / Guest)
# ---------------------------
elif st.session_state.page == "main":
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

menu_df = load_menu()

if not menu_df.empty:
    categories = menu_df["CATEGORY"].unique()
    for cat in categories:
        st.markdown(f"#### 🍽️ {cat}")
        cat_items = menu_df[menu_df["CATEGORY"] == cat]

        for _, row in cat_items.iterrows():
            col1, col2, col3 = st.columns([3, 1, 1])
            with col1:
                st.markdown(
                    f"<div style='font-size:16px; font-weight:500;'>{row['ITEM_NAME']}</div>",
                    unsafe_allow_html=True
                )
            with col2:
                st.markdown(
                    f"<div style='font-size:15px; color:#FFD700;'>₱{row['PRICE']:.2f}</div>",
                    unsafe_allow_html=True
                )
            with col3:
                if st.button("➕ Add", key=f"add_{row['ITEM_NAME']}", use_container_width=True):
                    if "cart" not in st.session_state:
                        st.session_state.cart = []
                    st.session_state.cart.append(row.to_dict())
                    st.success(f"✅ {row['ITEM_NAME']} added to cart!")
else:
    st.warning("⚠️ Menu is currently empty.")

                # CART DISPLAY
                if st.session_state.cart:
                    st.divider()
                    st.subheader("🛒 Your Cart")

                    cart_data = []
                    total_price = 0
                    for item, details in st.session_state.cart.items():
                        subtotal = details["qty"] * details["price"]
                        total_price += subtotal
                        cart_data.append({
                            "Item": item,
                            "Quantity": details["qty"],
                            "Price": f"₱{details['price']}",
                            "Subtotal": f"₱{subtotal}"
                        })

                    st.dataframe(pd.DataFrame(cart_data))
                    st.markdown(f"### 💵 Total: ₱{total_price}")

                    colX, colY = st.columns([1, 1])
                    with colX:
                        if st.button("🧾 Checkout"):
                            st.success("✅ Order placed successfully!")
                            st.session_state.cart.clear()
                    with colY:
                        if st.button("❌ Clear Cart"):
                            st.session_state.cart.clear()
                            st.info("Cart cleared.")
                else:
                    st.info("Your cart is empty.")
            else:
                st.info("No menu items available.")

        # --- RIGHT: SENTIMENT, FEEDBACKS, NOTIFICATIONS, HISTORY ---
        with right_col:
            # Sentiment & Feedbacks
            st.subheader("⭐ Feedbacks & Sentiment")
            if not is_guest:
                if not menu_df.empty:
                    with st.form("feedback_form"):
                        item_choice = st.selectbox("Which item?", menu_df["ITEM"].tolist(), key="feedback_item")
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

        # Logout Button
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
    user = st.session_state.user
    pending_cart = st.session_state.get("cart", {})

    if not pending_cart:
        st.warning("No pending order found. Go back to your cart.")
    else:
        total_cost = sum(v["qty"] * v["price"] for v in pending_cart.values())
        st.subheader("💳 Payment Confirmation")
        st.write(f"Total: ₱{total_cost}")
        method = st.radio("Payment Method", ["Cash", "GCash", "Card"], key="pay_method")

        if method == "Cash" and st.button("Confirm Cash Payment"):
            st.success("✅ Order confirmed! (Cash)")
            st.session_state.cart = {}
            st.session_state.page = "main"
            st.rerun()

        elif method == "GCash":
            st.image("https://via.placeholder.com/150?text=GCash+QR", caption="Scan QR to Pay")
            if st.button("Simulate GCash Payment Success"):
                st.success("✅ GCash Payment Successful!")
                st.session_state.cart = {}
                st.session_state.page = "main"
                st.rerun()

        elif method == "Card":
            st.text_input("Card Number")
            st.text_input("Expiry MM/YY")
            st.text_input("CVV")
            if st.button("Simulate Card Payment Success"):
                st.success("✅ Card Payment Successful!")
                st.session_state.cart = {}
                st.session_state.page = "main"
                st.rerun()
