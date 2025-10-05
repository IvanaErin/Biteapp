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
# ---------- NON-STAFF & GUEST PORTAL ----------
elif st.session_state.page == "main":
    # Ensure user/role are properly loaded
    if "user" not in st.session_state or not st.session_state.user:
        st.session_state.page = "login"
        st.rerun()

    user = st.session_state.user
    role = user.get("role", "Guest")
    is_guest = (role == "Guest")

    if role != "Staff":
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
                detected_category_col = detected_item_col = detected_price_col = None
            else:
                detected_category_col, detected_item_col, detected_price_col = detect_menu_columns(menu_df)
                if not detected_item_col or not detected_price_col:
                    st.write("Menu columns:", menu_df.columns.tolist())
                    st.error("Menu format not recognized. Missing item or price column.")
                    detected_category_col = detected_item_col = detected_price_col = None

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
                            st.markdown(f"<div style='font-size:16px; font-weight:500;'>{item_name}</div>", unsafe_allow_html=True)
                        with col2:
                            st.markdown(f"<div style='font-size:15px; color:#FFD700;'>₱{price_val:.2f}</div>", unsafe_allow_html=True)
                        with col3:
                            btn_key = f"add_{cat}_{item_name}"
                            if st.button("➕ Add", key=btn_key, use_container_width=True):
                                if item_name not in st.session_state.cart:
                                    st.session_state.cart[item_name] = {"qty": 1, "price": price_val}
                                else:
                                    st.session_state.cart[item_name]["qty"] += 1
                                st.success(f"✅ {item_name} added to cart!")

            # 🛒 CART
            if st.session_state.cart:
                st.divider()
                st.subheader("🛒 Your Cart")

                cart_data = []
                total_price = 0.0
                for item, details in st.session_state.cart.items():
                    qty = details.get("qty", 1)
                    price = details.get("price", 0.0)
                    subtotal = qty * float(price)
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
                        st.session_state.page = "payment"
                        st.rerun()
                with colY:
                    if st.button("❌ Clear Cart"):
                        st.session_state.cart.clear()
                        st.info("Cart cleared.")
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
            if st.session_state.notifications:
                for i, note in enumerate(st.session_state.notifications):
                    st.info(note, key=f"notif_{i}")
            else:
                st.info("No notifications.")
            if st.button("Clear notifications", key="clear_notifs"):
                st.session_state.notifications.clear()

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

        if method == "Cash":
            if st.button("Confirm Cash Payment"):
                order_id = f"ORD-{random.randint(100000,999999)}"
                save_receipt(order_id, pending_cart, total_cost, "Cash",
                             user.get("username", "Guest"), pickup_dt, "Completed")
                st.success("✅ Cash payment confirmed! Order recorded.")
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
                             user.get("username", "Guest"), pickup_dt, "Completed")
                st.success("✅ GCash payment confirmed! Thank you.")
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
                             user.get("username", "Guest"), pickup_dt, "Completed")
                st.success("✅ Card payment successful!")
                st.session_state.cart.clear()
                st.session_state.page = "main"
                st.rerun()

        st.divider()
        if st.button("⬅️ Back to Cart"):
            st.session_state.page = "main"
            st.rerun()
