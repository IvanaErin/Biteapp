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
        /* remove the default Streamlit header gap */
        [data-testid="stAppViewContainer"] > section:first-child {
            padding-top: 18px !important;
            margin-top: 0px !important;
        }

        /* hide builtin menu / footer if desired */
        #MainMenu { visibility: hidden; }
        footer { visibility: hidden; }

        /* login card appearance */
        .login-card {
            background: rgba(10,10,10,0.6);
            padding: 1.6rem;
            border-radius: 12px;
            max-width: 840px;
            margin: 18px auto;
            color: #fff;
            box-shadow: 0 8px 28px rgba(0,0,0,0.5);
        }

        /* uniform button sizing */
        div.stButton > button {
            width: 100%;
            height: 44px;
            font-size: 15px;
            border-radius: 8px;
        }

        /* inputs look */
        .stTextInput>div>div>input, .stTextInput>div>div>div>input {
            background: rgba(0,0,0,0.55);
            color: #fff;
        }

        /* make containers slightly translucent on top of background */
        .stContainer, .stMarkdown, .stExpander {
            color: #fff;
        }
        """
    )

    st.markdown("<style>" + "\n".join(css_parts) + "</style>", unsafe_allow_html=True)

# call background
set_background("back.jpg")

# ---------------------------
# DB CONNECTION (Snowflake) OR LOCAL FALLBACK
# ---------------------------
def get_connection():
    """Return a Snowflake connection or None if unavailable."""
    try:
        import snowflake.connector
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

# ---------------------------
# CRYPTO HELPERS (passwords)
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
# LOCAL DB FALLBACK
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

def update_loyalty_points(username: str, delta: int):
    conn = get_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE users SET loyalty_points = COALESCE(loyalty_points,0) + %s WHERE username=%s",
                (int(delta), username)
            )
            conn.commit()
            cur.execute("SELECT loyalty_points FROM users WHERE username=%s", (username,))
            r = cur.fetchone()
            return int(r[0] or 0) if r else None
        finally:
            cur.close()
            conn.close()
    else:
        _ensure_local_db()
        if username in st.session_state._local_accounts:
            acc = st.session_state._local_accounts[username]
            acc["loyalty_points"] = acc.get("loyalty_points", 0) + int(delta)
            return acc["loyalty_points"]
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
# RECEIPTS
# ---------------------------
def save_receipt(order_id: str, items: str, total: float, payment_method: str,
                 user_id: int = None, details: str = "", pickup_time=None, status="Pending"):
    conn = get_connection()
    if not conn:
        _ensure_local_db()
        st.session_state._local_receipts.append({
            "order_id": order_id,
            "items": items,
            "total": total,
            "payment_method": payment_method,
            "user_id": user_id,
            "details": details,
            "pickup_time": pickup_time,
            "status": status
        })
        return
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO receipts (order_id, items, total, payment_method, user_id, details, pickup_time, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (order_id, items, float(total), payment_method, user_id, details, pickup_time, status)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()

def load_receipts_df():
    conn = get_connection()
    if not conn:
        _ensure_local_db()
        rows = st.session_state._local_receipts
        return pd.DataFrame(rows) if rows else pd.DataFrame(
            columns=["order_id","items","total","payment_method","user_id","details","pickup_time","status"])
    try:
        cur = conn.cursor()
        cur.execute("SELECT order_id, items, total, payment_method, user_id, details, pickup_time, status, timestamp FROM receipts ORDER BY timestamp DESC")
        rows = cur.fetchall()
        return pd.DataFrame(rows, columns=["order_id","items","total","payment_method","user_id","details","pickup_time","status","timestamp"])
    finally:
        cur.close()
        conn.close()

def set_receipt_status(order_id: str, new_status: str):
    conn = get_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("UPDATE receipts SET status=%s WHERE order_id=%s", (new_status, order_id))
            conn.commit()
            return True
        finally:
            cur.close()
            conn.close()
    else:
        _ensure_local_db()
        for r in st.session_state._local_receipts:
            if r["order_id"] == order_id:
                r["status"] = new_status
                return True
        return False

# ---------------------------
# AI HELPER
# ---------------------------
def run_ai(question: str, extra_context: str = "") -> str:
    if not client:
        return "⚠️ AI unavailable (no Groq client configured)."
    if not question:
        return "Please ask a question."
    menu_text = ", ".join([f"{item} ({price})" for cat in menu_data.values() for item, price in cat.items()])
    context = f"MENU: {menu_text}\n{extra_context}"
    prompt = f"You are an assistant for a canteen. Context: {context}\nUser question: {question}"
    try:
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}]
        )
        return resp.choices[0].message.content
    except Exception as e:
        return f"⚠️ AI unavailable: {e}"

# --- Ensure menu_data exists ---
menu_data = {}

if os.path.exists("menu.csv"):
    menu_df = pd.read_csv("menu.csv")
    for cat, group in menu_df.groupby("Category"):
        menu_data[cat] = dict(zip(group["Item"], group["Price"]))
else:
    # Default menu if CSV doesn't exist
    default_menu = {
        "Breakfast": {"Pancakes": 50, "Omelette": 40},
        "Lunch": {"Burger": 80, "Pizza": 120},
        "Drinks": {"Coffee": 30, "Juice": 40},
        "Snacks": {"Chips": 20, "Donut": 25}
    }
    menu_data = default_menu.copy()
    # Save default menu to CSV
    menu_list = []
    for cat, items in default_menu.items():
        for item, price in items.items():
            menu_list.append({"Category": cat, "Item": item, "Price": price})
    pd.DataFrame(menu_list).to_csv("menu.csv", index=False)

# ---------------------------
# SESSION DEFAULTS
# ---------------------------
if "page" not in st.session_state:
    st.session_state.page = "login"
if "user" not in st.session_state:
    st.session_state.user = None
if "cart" not in st.session_state:
    st.session_state.cart = {}
if "sold_out" not in st.session_state:
    st.session_state.sold_out = set()
if "loyalty_points" not in st.session_state:
    st.session_state.loyalty_points = 0
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
# LOGIN & SIGNUP PAGES (Snowflake + hashed passwords)
# ---------------------------

# LOGIN PAGE
if st.session_state.page == "login":
    logo = Image.open("bite.jpg").resize((350, 150))
    col1, col2, col3 = st.columns([1,1,1])
    with col2:
        st.image(logo, use_container_width=False)

    username = st.text_input("Username", placeholder="Enter username", key="login_username")
    password = st.text_input("Password", type="password", placeholder="Enter password", key="login_password")

    col1, col2, col3, col4, col5 = st.columns([1,2,2,2,1])
    with col2:
        if st.button("Log In", use_container_width=True):
            try:
                acc = get_account(username)  # fetch from DB
                if acc and verify_password(acc["password"], password):
                    st.session_state.user = acc
                    st.session_state.page = "main"
                    st.success(f"✅ Welcome {acc['username']}!")
                    st.rerun()
                else:
                    st.error("❌ Invalid username or password.")
            except Exception as e:
                st.error(f"Login error: {e}")

    with col3:
        if st.button("Guest Account", use_container_width=True):
            st.session_state.user = {"username": "Guest", "role": "Non-Staff", "loyalty_points": 0}
            st.session_state.page = "main"
            st.rerun()

    with col4:
        if st.button("Create Account", use_container_width=True):
            st.session_state.page = "signup"
            st.rerun()


# SIGNUP PAGE
elif st.session_state.page == "signup":
    st.markdown('<div class="login-card">', unsafe_allow_html=True)
    st.markdown("<h2>✍️ Create Account</h2>", unsafe_allow_html=True)

    new_username = st.text_input("New Username", key="signup_username")
    new_pass = st.text_input("New Password", type="password", key="signup_password")
    new_role = st.selectbox("Role", ["Non-Staff", "Staff"], key="signup_role")

    # show password rules
    rules = password_valid_rules(new_pass)
    st.markdown("**Password rules:** (all must be ✅)")
    st.write(f"- Minimum 12 chars: {'✅' if rules['length'] else '❌'}")
    st.write(f"- Uppercase letter: {'✅' if rules['upper'] else '❌'}")
    st.write(f"- Lowercase letter: {'✅' if rules['lower'] else '❌'}")
    st.write(f"- Number: {'✅' if rules['digit'] else '❌'}")
    st.write(f"- Symbol: {'✅' if rules['symbol'] else '❌'}")

    if st.button("Register", use_container_width=True):
        if not new_username or not new_pass:
            st.error("Please fill all fields.")
        elif not all(rules.values()):
            st.error("Password does not meet requirements.")
        else:
            try:
                # check if username exists
                if get_account(new_username):
                    st.error("Username already exists.")
                else:
                    hashed_pw = hash_password(new_pass)
                    save_account(new_username, hashed_pw, new_role)
                    st.success("✅ Account created! Please log in.")
                    st.session_state.page = "login"
                    st.rerun()
            except Exception as e:
                st.error(f"Could not create account: {e}")

    if st.button("Back to Login", use_container_width=True):
        st.session_state.page = "login"
        st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)
# ---------------------------
# HELPER FUNCTIONS
# ---------------------------
def get_current_user():
    user = st.session_state.get("user")
    if not isinstance(user, dict):
        user = {"username": "Guest", "role": "Non-Staff", "loyalty_points": 0}
    user.setdefault("username", "Guest")
    user.setdefault("role", "Non-Staff")
    user.setdefault("loyalty_points", 0)
    return user

# ---------------------------
# Helper functions (you already have these in your code)
# ---------------------------
def run_ai(prompt, extra_context=None):
    # Simulate AI assistant
    return f"AI Response for: {prompt}" + (f" ({extra_context})" if extra_context else "")

def save_feedback(item, text, rating, user_id):
    # Add feedback to session state (replace with actual DB)
    if "feedbacks" not in st.session_state:
        st.session_state.feedbacks = []
    st.session_state.feedbacks.append({"item": item, "text": text, "rating": rating, "user": user_id})

def load_feedbacks_df():
    # Returns all feedbacks as DataFrame
    if "feedbacks" not in st.session_state:
        st.session_state.feedbacks = []
    return pd.DataFrame(st.session_state.feedbacks)

def save_receipt(order_id, items, total, method, user_id, pickup_dt, status):
    if "receipts" not in st.session_state:
        st.session_state.receipts = []
    st.session_state.receipts.append({
        "order_id": order_id,
        "items": items,
        "total": total,
        "method": method,
        "user": user_id,
        "pickup_dt": pickup_dt,
        "status": status,
        "timestamp": datetime.now()
    })

def load_receipts_df():
    if "receipts" not in st.session_state:
        st.session_state.receipts = []
    return pd.DataFrame(st.session_state.receipts)

def set_receipt_status(order_id, status):
    df = load_receipts_df()
    for rec in st.session_state.receipts:
        if rec["order_id"] == order_id:
            rec["status"] = status

# ---------------------------
# Session variables
# ---------------------------
if "cart" not in st.session_state:
    st.session_state.cart = {}
if "notifications" not in st.session_state:
    st.session_state.notifications = []
if "page" not in st.session_state:
    st.session_state.page = "main"
if "feedbacks" not in st.session_state:
    st.session_state.feedbacks = []

# ---------------------------
# USER SETUP
# ---------------------------
user = st.session_state.get("user") or {
    "username": "Guest",
    "role": "Guest",   # Guest, Non-Staff, Staff
    "loyalty_points": 0
}
role = user.get("role")
is_guest = role == "Guest"

# ---------------------------
# MAIN PORTAL
# ---------------------------
if st.session_state.page == "main":
    st.title(f"🏫 Welcome {user['username']} to BiteHub")

    if is_guest:
        st.warning("🔓 Guest session: no feedback, no loyalty points, orders not saved.")

    # ---------------------------
    # STAFF PORTAL
    # ---------------------------
    if role == "Staff":
        choice = st.sidebar.radio(
            "Staff Menu",
            ["Dashboard", "Pending Orders", "Manage Menu", "AI Assistant", "Feedback Review", "Sales Report"]
        )

        if choice == "Dashboard":
            st.subheader("📊 Staff Dashboard")
            receipts = load_receipts_df()
            fb = load_feedbacks_df()
            pending = receipts[receipts["status"].str.lower() == "pending"] if not receipts.empty else pd.DataFrame()
            st.metric("Total Orders", len(receipts))
            st.metric("Feedbacks", len(fb))
            st.metric("Pending Orders", len(pending))

        elif choice == "Pending Orders":
            st.subheader("📦 Pending Orders")
            receipts_df = load_receipts_df()
            if not receipts_df.empty:
                pending = receipts_df[receipts_df["status"].str.lower() == "pending"]
                if not pending.empty:
                    for _, row in pending.iterrows():
                        btn_key = f"ready_{row['order_id']}"
                        st.write(f"Order {row['order_id']}: {row['items']} — ₱{row['total']} | By: {row['user']} | Status: {row['status']}")
                        if st.button(f"Mark Ready {row['order_id']}", key=btn_key):
                            set_receipt_status(row['order_id'], "Ready for Pickup")
                            st.success(f"Order {row['order_id']} marked ready")
                            st.rerun()
                else:
                    st.info("No pending orders.")
            else:
                st.info("No receipts yet.")

        elif choice == "Manage Menu":
            st.subheader("📖 Manage Menu")
            menu_edit_df = pd.DataFrame([
                {"Category": cat, "Item": item, "Price": price}
                for cat, items in menu_data.items()
                for item, price in items.items()
            ])
            edited_df = st.data_editor(menu_edit_df, num_rows="dynamic", use_container_width=True)
            if st.button("💾 Save Menu Updates"):
                new_menu = {}
                for _, row in edited_df.iterrows():
                    if row["Category"] not in new_menu:
                        new_menu[row["Category"]] = {}
                    new_menu[row["Category"]][row["Item"]] = row["Price"]
                menu_data.clear()
                menu_data.update(new_menu)
                st.success("✅ Menu updated!")
                st.rerun()

        elif choice == "AI Assistant":
            st.subheader("🤖 Staff AI Assistant")
            q = st.text_input("Ask AI about sales, menu trends, or customer feedback:")
            if st.button("Ask Staff AI") and q:
                answer = run_ai(q, extra_context="STAFF MODE: Provide analytics insights")
                st.markdown(f"<div style='color:white; font-size:16px'>{answer}</div>", unsafe_allow_html=True)

        elif choice == "Feedback Review":
            st.subheader("📝 All Customer Feedback")
            fb_df = load_feedbacks_df()
            if not fb_df.empty:
                st.dataframe(fb_df, use_container_width=True)
            else:
                st.info("No feedback received yet.")

        elif choice == "Sales Report":
            st.subheader("💹 Sales Report")
            receipts = load_receipts_df()
            if not receipts.empty:
                category_sales = {}
                for cat, items in menu_data.items():
                    total_cat = sum(
                        receipts.apply(lambda r: sum(r['items'].count(item)*r['total']/len(r['items'].split(',')) if item in r['items'] else 0, axis=0), axis=0)
                        for item in items
                    )
                    category_sales[cat] = total_cat
                fig, ax = plt.subplots()
                ax.pie(category_sales.values(), labels=category_sales.keys(), autopct="%1.1f%%", startangle=90)
                ax.set_title("Sales by Menu Category")
                st.pyplot(fig)
            else:
                st.info("No sales data yet.")

        if st.button("Log Out", key="logout_staff"):
            st.session_state.page = "login"
            st.session_state.user = None
            st.rerun()

    # ---------------------------
    # NON-STAFF PORTAL
    # ---------------------------
    elif role == "Non-Staff":
        col_left, col_right = st.columns([2, 1])

        with col_left:
            st.subheader("🤖 Canteen AI Assistant")
            q = st.text_input("Ask about menu, budget, feedback, or ordering:", key="ai_query_main")
            if st.button("Ask AI", key="ai_button_main"):
                extra = ""
                try:
                    sales_df = load_receipts_df()
                    feedback_df = load_feedbacks_df()
                    extra = f"SALES_SUMMARY: {sales_df.head(10).to_dict() if not sales_df.empty else 'No sales'}\nFEEDBACK_SUMMARY: {feedback_df.head(10).to_dict() if not feedback_df.empty else 'No feedback'}"
                except Exception:
                    extra = "DB context unavailable."
                with st.spinner("Asking AI..."):
                    st.info(run_ai(q, extra))

            st.divider()
            st.subheader("📋 Full Menu")
            for cat, items in menu_data.items():
                with st.expander(cat, expanded=False):
                    for item_name, price in items.items():
                        add_key = f"add_{cat}_{item_name}".replace(" ", "_")
                        if st.button(f"Add {item_name} — ₱{price}", key=add_key):
                            st.session_state.cart[item_name] = st.session_state.cart.get(item_name, 0) + 1
                            st.success(f"Added 1 x {item_name}")
                            st.rerun()

            # Cart + Checkout
            st.divider()
            st.subheader("🛒 Your Cart")
            if st.session_state.cart:
                total = sum(
                    next((menu_data[cat][item] for cat in menu_data if item in menu_data[cat]), 0) * qty
                    for item, qty in st.session_state.cart.items()
                )
                st.write(f"**Subtotal: ₱{total}**")
                pickup_date = st.date_input("Pickup date", value=date.today(), key="pickup_date")
                pickup_time = st.time_input("Pickup time", value=datetime.now().time(), key="pickup_time")
                payment_method = st.radio("Select Payment Method:", ["Cash", "GCash", "Card"], key="pay_method")

                # ---------------------------
                # Proceed to Payment (save pending order first)
                # ---------------------------
                if st.button("Proceed to Payment", key="checkout_btn"):
                    order_id = f"ORD{random.randint(10000,99999)}"
                    items_str = ", ".join([f"{i} x{q}" for i,q in st.session_state.cart.items()])

                    # Save as Pending in DB
                    save_receipt(
                        order_id=order_id,
                        items=items_str,
                        total=total,
                        payment_method="Pending",
                        user_id=user["username"],
                        pickup_dt=datetime.combine(pickup_date, pickup_time),
                        status="Pending"
                    )

                    # Store pending order in session for actual payment
                    st.session_state.pending_order = {
                        "order_id": order_id,
                        "items": dict(st.session_state.cart),
                        "total": total,
                        "pickup_dt": datetime.combine(pickup_date, pickup_time),
                        "payment_method": payment_method,
                        "user_id": user["username"]
                    }
                    st.session_state.page = "payment"
                    st.success("Go to the Payment page to complete your order.")

        # RIGHT: Feedback + Notifications + History
        with col_right:
            st.subheader("📢 Notifications")
            for note in st.session_state.notifications:
                st.info(note)
            if st.button("Clear notifications", key="clear_notifs"):
                st.session_state.notifications.clear()

            st.divider()
            st.subheader("📜 Order History")
            history = load_receipts_df()
            if not history.empty:
                user_orders = history[history["user"] == user["username"]]
                if not user_orders.empty:
                    st.dataframe(user_orders.sort_values(by="timestamp", ascending=False), use_container_width=True)
                else:
                    st.info("No past orders yet.")
            else:
                st.info("No orders have been made yet.")

    # ---------------------------
    # GUEST PORTAL
    # ---------------------------
    elif is_guest:
        col_left, col_right = st.columns([2,1])

        with col_left:
            st.subheader("🤖 Canteen AI Assistant")
            q = st.text_input("Ask about menu or budget:", key="ai_query_guest")
            if st.button("Ask AI", key="ai_button_guest"):
                answer = run_ai(q)
                st.info(answer)

            st.divider()
            st.subheader("📋 Full Menu (Guest)")
            for cat, items in menu_data.items():
                with st.expander(cat):
                    for item_name, price in items.items():
                        st.write(f"{item_name} — ₱{price}")
            st.info("⚠️ Guests cannot place orders, leave feedback, or earn loyalty points.")

        with col_right:
            st.subheader("📢 Notifications")
            st.info("Guests cannot receive notifications or order history.")

# ---------------------------
# PAYMENT PAGE
# ---------------------------
elif st.session_state.page == "payment":
    pending = st.session_state.get("pending_order", {})
    if not pending:
        st.warning("No pending order found. Go back to your cart.")
    else:
        st.subheader("💳 Payment Confirmation")
        st.write(f"Total: ₱{pending['total']}")
        st.write(f"Payment Method: {pending['payment_method']}")

        if pending["payment_method"] == "Cash":
            if st.button("Confirm Cash Payment"):
                # Update status from Pending -> Paid
                save_receipt(
                    order_id=pending["order_id"],
                    items=", ".join([f"{i} x{q}" for i,q in pending["items"].items()]),
                    total=pending["total"],
                    payment_method="Cash",
                    user_id=pending["user_id"],
                    pickup_dt=pending["pickup_dt"],
                    status="Paid"
                )
                st.session_state.loyalty_points = st.session_state.get("loyalty_points", 0) + pending['total']//100
                st.success(f"Order confirmed! Order ID: {pending['order_id']}")
                st.session_state.cart = {}
                st.session_state.pending_order = {}
                st.session_state.page = "main"

        elif pending["payment_method"] in ["GCash", "Card"]:
            if pending["payment_method"] == "GCash":
                st.image("https://via.placeholder.com/150?text=GCash+QR", caption="Scan QR to Pay")
            elif pending["payment_method"] == "Card":
                st.text_input("Card Number", key="card_number")
                st.text_input("Expiry MM/YY", key="card_expiry")
                st.text_input("CVV", key="card_cvv")

            if st.button("Simulate Payment Success"):
                save_receipt(
                    order_id=pending["order_id"],
                    items=", ".join([f"{i} x{q}" for i,q in pending["items"].items()]),
                    total=pending["total"],
                    payment_method=pending["payment_method"],
                    user_id=pending["user_id"],
                    pickup_dt=pending["pickup_dt"],
                    status="Paid"
                )
                st.session_state.loyalty_points = st.session_state.get("loyalty_points", 0) + pending['total']//100
                st.success(f"Payment confirmed! Order ID: {pending['order_id']}")
                st.session_state.notifications.append(
                    f"Order {pending['order_id']} placed — Payment: {pending['payment_method']} — Pickup: {pending['pickup_dt']}"
                )
                st.session_state.cart = {}
                st.session_state.pending_order = {}
                st.session_state.page = "main"

        # GCash or Card Payment
        elif pending["payment_method"] in ["GCash", "Card"]:
            if pending["payment_method"] == "GCash":
                st.image("https://via.placeholder.com/150?text=GCash+QR", caption="Scan QR to Pay")
            elif pending["payment_method"] == "Card":
                st.text_input("Card Number", key="card_number")
                st.text_input("Expiry MM/YY", key="card_expiry")
                st.text_input("CVV", key="card_cvv")

            if st.button("Simulate Payment Success"):
                order_id = f"ORD{random.randint(10000,99999)}"
                items_str = ", ".join([f"{i} x{q}" for i,q in pending["items"].items()])
                
                # Save receipt only if not guest
                if not is_guest:
                    save_receipt(order_id, items_str, pending["total"], pending["payment_method"], pending["user_id"], pending["pickup_dt"], "Paid")
                    # Add loyalty points: 1 point per ₱100 spent
                    st.session_state.loyalty_points = st.session_state.get("loyalty_points", 0) + pending['total']//100

                st.success(f"Payment confirmed! Order ID: {order_id}")
                st.download_button(
                    "Download Receipt (txt)",
                    data=f"Order ID: {order_id}\nUser: {pending['user_id']}\nItems: {items_str}\nTotal: ₱{pending['total']}\nPayment: {pending['payment_method']}\nPickup: {pending['pickup_dt']}\nStatus: Paid",
                    file_name=f"{order_id}_receipt.txt"
                )

                st.session_state.notifications.append(f"Order {order_id} placed — Payment: {pending['payment_method']} — Pickup: {pending['pickup_dt']}")
                st.session_state.cart = {}
                st.session_state.pending_order = {}
                st.session_state.page = "main"

# ---------------------------
# STAFF PORTAL
# ---------------------------
elif user["role"] == "Staff":
    st.title("🛠️ BiteHub Staff Portal")

    # --- Load menu CSV (editable only by staff) ---
    default_menu = {
        "Breakfast": {"Pancakes": 50, "Omelette": 40},
        "Lunch": {"Burger": 80, "Pizza": 120},
        "Drinks": {"Coffee": 30, "Juice": 40},
        "Snacks": {"Chips": 20, "Donut": 25}
    }

    if not os.path.exists("menu.csv"):
        menu_list = []
        for cat, items in default_menu.items():
            for item, price in items.items():
                menu_list.append({"Category": cat, "Item": item, "Price": price})
        pd.DataFrame(menu_list).to_csv("menu.csv", index=False)

    menu_df = pd.read_csv("menu.csv")
    menu_data = {}
    for cat, group in menu_df.groupby("Category"):
        menu_data[cat] = dict(zip(group["Item"], group["Price"]))

    choice = st.sidebar.radio(
        "Staff Menu", 
        ["Dashboard", "Pending Orders", "Manage Menu", "AI Assistant", "Feedback Review", "Sales Report"]
    )

    # --- Manage Menu ---
    if choice == "Manage Menu":
        st.subheader("📖 Manage Menu")
        st.info("Add or update menu items")
        menu_edit_df = pd.DataFrame([
            {"Category": cat, "Item": item, "Price": price}
            for cat, items in menu_data.items()
            for item, price in items.items()
        ])
        edited_df = st.data_editor(menu_edit_df, num_rows="dynamic", use_container_width=True)

        if st.button("💾 Save Menu Updates"):
            # Convert edited DataFrame back to nested dictionary
            new_menu = {}
            menu_list_to_save = []
            for _, row in edited_df.iterrows():
                cat = row["Category"]
                item = row["Item"]
                price = row["Price"]

                if cat not in new_menu:
                    new_menu[cat] = {}
                new_menu[cat][item] = price

                # Prepare list for CSV saving
                menu_list_to_save.append({"Category": cat, "Item": item, "Price": price})

            # Update in-memory menu
            menu_data.clear()
            menu_data.update(new_menu)

            # Save to CSV for persistence
            pd.DataFrame(menu_list_to_save).to_csv("menu.csv", index=False)

            st.success("✅ Menu updated and saved!")
            st.rerun()
            
    choice = st.sidebar.radio(
        "Staff Menu", 
        ["Dashboard", "Pending Orders", "Manage Menu", "AI Assistant", "Feedback Review", "Sales Report"]
    )

    if choice == "Dashboard":
        st.subheader("📊 Staff Dashboard")
        st.info("Overview: pending orders, quick sales, and recent feedback.")
        receipts = load_receipts_df()
        fb = load_feedbacks_df()
        st.metric("Total Orders", len(receipts))
        st.metric("Feedbacks", len(fb))
        pending = receipts[receipts["status"].str.lower() == "pending"] if not receipts.empty else pd.DataFrame()
        st.metric("Pending Orders", len(pending))

    elif choice == "Pending Orders":
        st.subheader("📦 Pending Orders")
        receipts_df = load_receipts_df()
        if not receipts_df.empty:
            pending = receipts_df[receipts_df["status"].str.lower() == "pending"]
            if not pending.empty:
                for _, row in pending.iterrows():
                    btn_key = f"ready_{row['order_id']}"
                    st.write(f"Order {row['order_id']}: {row['items']} — ₱{row['total']} | By: {row['user']} | Status: {row['status']}")
                    if st.button(f"Mark Ready {row['order_id']}", key=btn_key):
                        set_receipt_status(row['order_id'], "Ready for Pickup")
                        st.success(f"Order {row['order_id']} marked ready")
                        st.rerun()
            else:
                st.info("No pending orders.")
        else:
            st.info("No receipts yet.")

    elif choice == "AI Assistant":
        st.subheader("🤖 Staff AI Assistant")
        q = st.text_input("Ask AI about sales, menu trends, or customer feedback:")
        if st.button("Ask Staff AI") and q:
            answer = run_ai(q, extra_context="STAFF MODE: Provide analytics insights")
            st.markdown(f"<div style='color:white; font-size:16px'>{answer}</div>", unsafe_allow_html=True)

    elif choice == "Feedback Review":
        st.subheader("📝 All Customer Feedback")
        fb_df = load_feedbacks_df()
        if not fb_df.empty:
            st.dataframe(fb_df, use_container_width=True)
        else:
            st.info("No feedback received yet.")

    elif choice == "Sales Report":
        st.subheader("💹 Sales Report")
        receipts = load_receipts_df()
        if not receipts.empty:
            # Pie chart: sales per category
            category_sales = {}
            for cat, items in menu_data.items():
                total_cat = sum(
                    receipts.apply(lambda r: sum(r['items'].count(item)*r['total']/len(r['items'].split(',')) if item in r['items'] else 0, axis=0), axis=0)
                    for item in items
                )
                category_sales[cat] = total_cat
            fig, ax = plt.subplots()
            ax.pie(category_sales.values(), labels=category_sales.keys(), autopct="%1.1f%%", startangle=90)
            ax.set_title("Sales by Menu Category")
            st.pyplot(fig)
        else:
            st.info("No sales data yet.")
            
    if st.button("Log Out", key="logout_staff"):
        st.session_state.page = "login"
        st.session_state.user = None
        st.rerun()
