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
    """
    Sets a base64 background if image_file is present.
    If not present, do nothing (keeps Streamlit default).
    """
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

    # common UI CSS
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
# LOGIN PAGE
# ---------------------------
if st.session_state.page == "login":
    st.markdown("<h1 style='text-align: center; color: white;'>☕ BiteHub — Login</h1>", unsafe_allow_html=True)
    with st.container():
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        if st.button("Login"):
            acc = validate_account(username, password)
            if acc:
                st.session_state.user = acc
                st.session_state.page = "main"
                st.experimental_rerun()
            else:
                st.error("Invalid username or password.")
        if st.button("Sign up"):
            st.session_state.page = "signup"

# ---------------------------
# SIGNUP PAGE
# ---------------------------
elif st.session_state.page == "signup":
    st.markdown("<h1 style='text-align: center; color: white;'>📝 BiteHub — Signup</h1>", unsafe_allow_html=True)
    with st.container():
        new_user = st.text_input("New Username")
        new_pass = st.text_input("New Password", type="password")
        confirm_pass = st.text_input("Confirm Password", type="password")
        if st.button("Create Account"):
            if not new_user or not new_pass:
                st.error("Username and password required.")
            elif new_pass != confirm_pass:
                st.error("Passwords do not match.")
            elif get_account(new_user):
                st.error("Username already exists.")
            else:
                hashed = hash_password(new_pass)
                save_account(new_user, hashed, "Non-Staff")
                st.success("Account created! Please login.")
                st.session_state.page = "login"

    if st.button("Back to Login"):
        st.session_state.page = "login"

# ---------------------------
# MAIN PORTAL
# ---------------------------
elif st.session_state.page == "main":
    user = st.session_state.user or {"username": "Guest", "role": "Guest"}
    role = user["role"]

    st.title(f"🏫 Welcome {user['username']} to BiteHub")

    if role == "Staff":
        # STAFF PORTAL
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
            # TODO: add staff analytics
        elif choice == "Pending Orders":
            st.subheader("⏳ Pending Orders")
        elif choice == "Manage Menu":
            st.subheader("📖 Manage Menu")
            menu_df = load_menu()
            edited = st.data_editor(menu_df, num_rows="dynamic", use_container_width=True)
            if st.button("Save Menu Changes"):
                upsert_menu(edited)
                st.success("✅ Menu updated and saved!")
        elif choice == "AI Assistant":
            q = st.text_area("Ask AI something:")
            if st.button("Ask AI"):
                st.write(run_ai(q))
        elif choice == "Feedback Review":
            fb = load_feedbacks_df()
            st.dataframe(fb, use_container_width=True)
        elif choice == "Sales Report":
            st.subheader("💹 Sales Report")
            # TODO: add sales visualization

    else:
        # NON-STAFF + GUEST PORTAL
        col1, col2 = st.columns([2,1])

st.subheader("📖 Menu & Ordering")
menu_df = load_menu()

# Initialize cart
if "cart" not in st.session_state:
    st.session_state.cart = {}

# Show menu items with inline add-to-cart buttons
for idx, row in menu_df.iterrows():
    cols = st.columns([3, 1, 1])
    with cols[0]:
        st.write(row["ITEM"])
    with cols[1]:
        st.write(f"₱{row['PRICE']}")
    with cols[2]:
        if st.button("Add", key=f"add_{idx}"):
            st.session_state.cart[row["ITEM"]] = st.session_state.cart.get(row["ITEM"], 0) + 1

st.divider()
st.subheader("🛒 Your Cart")
if st.session_state.cart:
    for item, qty in list(st.session_state.cart.items()):
        cols = st.columns([3, 1, 1, 1])
        with cols[0]:
            st.write(item)
        with cols[1]:
            st.write(f"x{qty}")
        with cols[2]:
            if st.button("+", key=f"plus_{item}"):
                st.session_state.cart[item] += 1
        with cols[3]:
            if st.button("🗑️", key=f"remove_{item}"):
                del st.session_state.cart[item]
else:
    st.info("Your cart is empty.")

if st.button("Proceed to Payment"):
    if not st.session_state.cart:
        st.warning("Your cart is empty!")
    else:
        st.session_state.page = "payment"
        st.experimental_rerun()
        
        # --------- RIGHT SIDE (Sentiment + Feedback + Notifications + Order History)
        with col2:
            st.subheader("📊 Sentiment Analysis")
            st.info("AI-powered analysis of canteen feedback coming soon.")

            st.divider()
            st.subheader("⭐ Feedbacks")
            if role != "Guest":
                item_choice = st.text_input("Item")
                feedback = st.text_area("Your Feedback")
                rating = st.slider("Rating", 1, 5, 3)
                if st.button("Submit Feedback", key="feedback_btn") and feedback and item_choice:
                    save_feedback(
                        item=item_choice,
                        feedback=feedback,
                        rating=rating,
                        user_id=user["username"]
                    )
                    st.success("✅ Feedback submitted!")
            else:
                st.info("Guests cannot submit feedback.")

            st.divider()
            st.subheader("📢 Notifications")
            if not st.session_state.notifications:
                st.info("No notifications yet.")
            else:
                for note in st.session_state.notifications:
                    st.info(note)
                if st.button("Clear notifications"):
                    st.session_state.notifications.clear()

            st.divider()
            st.subheader("📜 Order History")
            history = pd.DataFrame(st.session_state.get("_local_receipts", []))
            if not history.empty and role != "Guest":
                st.dataframe(history, use_container_width=True)
            elif role == "Guest":
                st.info("Guests cannot save order history.")
            else:
                st.info("No past orders yet.")

        st.divider()
        if st.button("🚪 Log Out"):
            st.session_state.page = "login"
            st.session_state.user = None
            st.experimental_rerun()

# ---------------------------
# PAYMENT PAGE
# ---------------------------
elif st.session_state.page == "payment":
    pending = {
        "order_id": random.randint(1000,9999),
        "items": st.session_state.cart,
        "total": sum(50 for _ in st.session_state.cart), # placeholder
        "payment_method": "Cash",
        "user_id": st.session_state.user["username"] if st.session_state.user else "Guest",
        "pickup_dt": datetime.now().strftime("%Y-%m-%d %H:%M")
    }

    if not pending["items"]:
        st.warning("No pending order found. Go back to your cart.")
    else:
        st.subheader("💳 Payment Confirmation")
        st.write(f"Total: ₱{pending['total']}")
        method = st.radio("Payment Method", ["Cash", "GCash", "Card"], key="pay_method")
        pending["payment_method"] = method

        if method == "Cash":
            if st.button("Confirm Cash Payment"):
                st.success(f"Order confirmed! Order ID: {pending['order_id']}")
                st.session_state.cart = {}
                st.session_state.page = "main"
        elif method == "GCash":
            st.image("https://via.placeholder.com/150?text=GCash+QR", caption="Scan QR to Pay")
            if st.button("Simulate GCash Payment Success"):
                st.success(f"Order confirmed! Order ID: {pending['order_id']}")
                st.session_state.cart = {}
                st.session_state.page = "main"
        elif method == "Card":
            st.text_input("Card Number")
            st.text_input("Expiry MM/YY")
            st.text_input("CVV")
            if st.button("Simulate Card Payment Success"):
                st.success(f"Order confirmed! Order ID: {pending['order_id']}")
                st.session_state.cart = {}
                st.session_state.page = "main"
