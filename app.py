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

# ----------------- AI CLIENT -----------------
client = Groq(api_key=st.secrets["GROQ_API_KEY"])

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


# call background (make sure can.jpg exists or pass None)
set_background("cof.jpg")

# ---------------------------
# DB CONNECTION (Snowflake) OR LOCAL FALLBACK
# ---------------------------
def get_connection():
    try:
        conn = snowflake.connector.connect(
            user=os.environ.get("SNOWFLAKE_USER"),
            password=os.environ.get("SNOWFLAKE_PASSWORD"),
            account=os.environ.get("SNOWFLAKE_ACCOUNT"),
            warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE"),
            database=os.environ.get("SNOWFLAKE_DATABASE"),
            schema=os.environ.get("SNOWFLAKE_SCHEMA"),
        )
        return conn
    except Exception as e:
        print("❌ Could not connect to Snowflake:", e)
        return None

# ---------------------------
# CRYPTO HELPERS (password)
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


import streamlit as st
import pandas as pd

# ---------------------------
# LOCAL DB FALLBACK
# ---------------------------
def _ensure_local_db():
    if "_local_accounts" not in st.session_state:
        st.session_state._local_accounts = {}  # username -> {password, role, loyalty_points}
    if "_local_feedbacks" not in st.session_state:
        st.session_state._local_feedbacks = []  # list of dicts
    if "_local_receipts" not in st.session_state:
        st.session_state._local_receipts = []  # list of dicts

# ---------------------------
# DB CONNECTION
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
            return {"id": row[0], "username": row[1], "password": row[2], "role": row[3], "loyalty_points": row[4]}
        return None
    finally:
        cur.close()
        conn.close()

def validate_account(username: str, password: str):
    acc = get_account(username)
    if acc and acc["password"] == password:
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
def save_receipt(order_id: str, items: str, total: float, payment_method: str, user_id: int = None, details: str = "", pickup_time=None, status="Pending"):
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
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["order_id","items","total","payment_method","user_id","details","pickup_time","status"])
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

def set_receipt_status(order_id: str, new_status: str):
    conn = get_connection()
    if conn:
        cur = conn.cursor()
        try:
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
# AI helper (best-effort)
# ---------------------------
try:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"]) if Groq and "GROQ_API_KEY" in st.secrets else None
except Exception:
    client = None


def run_ai(question: str, extra_context: str = "") -> str:
    if not client:
        return "⚠️ AI unavailable (no Groq client configured)."
    if not question:
        return "Please ask a question."
    menu_text = ", ".join([f"{item} ({price})" for cat in menu_data.values() for item, price in cat.items()])
    context = f"MENU: {menu_text}\n{extra_context}"
    prompt = f"You are an assistant for a canteen. Context: {context}\nUser question: {question}"
    try:
        resp = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": prompt}])
        return resp.choices[0].message.content
    except Exception as e:
        return f"⚠️ AI unavailable: {e}"


# ---------------------------
# MENU + initial session keys
# ---------------------------
menu_data = {
    "Breakfast": {"Tapsilog": 70, "Longsilog": 65, "Hotdog Meal": 50, "Omelette": 45},
    "Lunch": {"Chicken Adobo": 90, "Pork Sinigang": 100, "Beef Caldereta": 120, "Rice": 15},
    "Snack": {"Burger": 50, "Fries": 30, "Siomai Rice": 60, "Spaghetti": 45},
    "Drinks": {"Soda": 20, "Iced Tea": 25, "Bottled Water": 15, "Coffee": 30},
    "Dessert": {"Halo-Halo": 65, "Leche Flan": 40, "Ice Cream": 35},
    "Dinner": {"Grilled Chicken": 95, "Sisig": 110, "Fried Bangus": 85, "Rice": 15},
}

# session defaults
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
# Password rules helper
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
        <h1 style='text-align: center; color: white; margin-top: -20px;'>
            ☕ BiteHub — Login
        </h1>
        """,
        unsafe_allow_html=True
    )


    username = st.text_input("Username", placeholder="Enter username", key="login_username")
    password = st.text_input("Password", type="password", placeholder="Enter password", key="login_password")

    # centered buttons with consistent width
    col1, col2, col3, col4, col5 = st.columns([1, 2, 2, 2, 1])
    with col2:
        if st.button("Log In", use_container_width=True):
            try:
                user = validate_account(username, password)
            except Exception as e:
                st.error(f"Login error: {e}")
                user = None
            if user:
                st.session_state.user = user
                st.session_state.page = "main"
                st.rerun()
            else:
                st.error("❌ Invalid username or password. Please try again or create an account.")
    with col3:
        if st.button("Guest Account", use_container_width=True):
            st.session_state.user = {"username": "Guest", "role": "Non-Staff", "loyalty_points": 0}
            st.session_state.page = "main"
            st.rerun()
    with col4:
        if st.button("Create Account", use_container_width=True):
            st.session_state.page = "signup"
            st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)


# ---------------------------
# UI: SIGNUP PAGE
# ---------------------------
elif st.session_state.page == "signup":
    st.markdown('<div class="login-card">', unsafe_allow_html=True)
    st.markdown("<h2>✍️ Create Account</h2>", unsafe_allow_html=True)

    new_username = st.text_input("New Username", key="signup_username")
    new_pass = st.text_input("New Password", type="password", key="signup_password")
    new_role = st.selectbox("Role", ["Non-Staff", "Staff"], key="signup_role")

    rules = password_valid_rules(new_pass)
    st.markdown("**Password rules:** (all must be ✅ to register)")
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
                if get_account(new_username):
                    st.error("Username already exists.")
                else:
                    save_account(new_username, new_pass, new_role)
                    # ✅ Redirect back to login instead of auto-login
                    st.success("✅ Account created successfully! Please log in with your new account.")
                    st.session_state.page = "login"
                    st.rerun()
            except Exception as e:
                st.error(f"Could not create account: {e}")

    if st.button("Back to Login", use_container_width=True):
        st.session_state.page = "login"
        st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)


# ---------------------------
# UI: MAIN PORTAL (Non-Staff + Staff)
# ---------------------------
elif st.session_state.page == "main":
    user = st.session_state.user or {"username": "Guest", "role": "Non-Staff", "loyalty_points": 0}
    if "loyalty_points" not in user:
        user["loyalty_points"] = user.get("loyalty_points", 0)

    # Guest banner
    if user["username"] == "Guest":
        st.warning("🔓 You're on a Guest session. Create an account to enjoy loyalty points, promos, and feedback posting.")

    st.title(f"🏫 Welcome {user['username']} to BiteHub")

# Columns layout
col_left, col_right = st.columns([1, 1])

with col_left:
    st.subheader("🤖 Canteen AI Assistant")
    q = st.text_input("Ask about menu, budget, or ordering:", key="ai_query_main")
    
    if st.button("Ask AI", key="ai_button_main"):
        with st.spinner("Asking AI..."):
            answer = run_ai(q)
            st.markdown(f"<div style='color: black; font-size:16px'>{answer}</div>", unsafe_allow_html=True)

    # RIGHT: Sentiment analysis
    with col_right:
        st.subheader("📝 Feedback Sentiment Analysis")
        # Only show if a product has feedback
        for item_name, qty in st.session_state.cart.items():
            fb_key = f"feedback_{item_name}"
            feedback_text = st.text_area(f"Your feedback for {item_name}:", key=fb_key)
            if feedback_text:
                if st.button(f"Analyze Sentiment for {item_name}", key=f"analyze_{item_name}"):
                    prompt = f"""
                    You are a sentiment analysis assistant.
                    The user gave this feedback for {item_name}: "{feedback_text}"
                    Classify sentiment as Positive 😊, Negative 😡, or Neutral 😐.
                    """
                    if client:
                        resp = client.chat.completions.create(
                            model="llama-3.1-8b-instant",
                            messages=[{"role": "user", "content": prompt}]
                        )
                        st.success(resp.choices[0].message.content)
                    else:
                        st.warning("Sentiment AI unavailable")
            else:
                st.info(f"Waiting for feedback on {item_name}...")

    # Non-Staff UX
    if user["role"] == "Non-Staff":
        is_guest = user["username"] == "Guest"
        colA, colB = st.columns([2, 1])
        with colA:
            st.subheader("📋 Menu")
            for cat, items in menu_data.items():
                with st.expander(cat, expanded=False):
                    for item_name, price in items.items():
                        if item_name in st.session_state.sold_out:
                            st.write(f"~~{item_name}~~ — Sold out")
                            continue
                        cols = st.columns([1, 1, 1])
                        qty_key = f"qty_{cat}_{item_name}"
                        qty = cols[0].number_input(f"{item_name} (₱{price})", min_value=0, value=0, step=1, key=qty_key)
                        if cols[1].button("Add", key=f"add_{cat}_{item_name}") and qty > 0:
                            st.session_state.cart[item_name] = st.session_state.cart.get(item_name, 0) + qty
                            st.success(f"Added {qty} x {item_name}")

            # cart summary & checkout
            if st.session_state.cart:
                st.subheader("🛒 Your Cart")
                total = 0
                for it, qtt in st.session_state.cart.items():
                    price = next((p for cat in menu_data.values() for n, p in cat.items() if n == it), 0)
                    st.write(f"{it} x {qtt} = ₱{price * qtt}")
                    total += price * qtt

                st.write(f"**Subtotal: ₱{total}**")

                # loyalty points
                user_points = 0
                if not is_guest:
                    try:
                        db_acc = get_account(user["username"])
                        user_points = db_acc.get("loyalty_points", 0) if db_acc else 0
                    except Exception:
                        user_points = st.session_state.loyalty_points
                    st.write(f"🔖 Points available: {user_points} pts (100 pts = ₱1)")

                # redeem options
                discount = 0
                applied_points = 0
                if not is_guest:
                    tier_options = []
                    if user_points >= 500:
                        tier_options.append(("Use 500 pts → ₱10 discount", 10, 500))
                    if user_points >= 200:
                        tier_options.append(("Use 200 pts → ₱3 discount", 3, 200))
                    if user_points >= 100:
                        tier_options.append(("Use 100 pts → ₱1 discount", 1, 100))
                    if tier_options:
                        st.markdown("**Redeem points for preset discounts:**")
                        chosen = st.selectbox("Choose redemption (optional)", ["None"] + [t[0] for t in tier_options], key="redeem_choice")
                        if chosen != "None":
                            for label, disc_val, pts_req in tier_options:
                                if label == chosen:
                                    discount = disc_val
                                    applied_points = pts_req
                                    break

                final_total = max(0, total - discount)
                st.write(f"**Total after discount: ₱{final_total}**")

                pickup_date = st.date_input("Pickup date (optional)", value=date.today(), key="pickup_date")
                pickup_time = st.time_input("Pickup time (optional)", value=datetime.now().time(), key="pickup_time")

                payment_method = st.radio("Payment Method", ["Cash", "Card", "E-Wallet"], key="pmethod")
                payment_details = ""
                if payment_method == "Card":
                    payment_details = st.text_input("Card Number (mock)", key="card_num")
                elif payment_method == "E-Wallet":
                    payment_details = st.selectbox("E-Wallet", ["GCash", "Maya", "QR Scan"], key="ewallet_type")

                if st.button("Place Order", key="place_order_nonstaff"):
                    order_id = f"ORD{random.randint(10000,99999)}"
                    items_str = ", ".join([f"{k}x{v}" for k, v in st.session_state.cart.items()])
                    pickup_dt = datetime.combine(pickup_date, pickup_time)
                    details = f"user:{user['username']}|notes:pickup scheduled"
                    try:
                        save_receipt(order_id, items_str, final_total, payment_method, details, pickup_time=pickup_dt, status="Pending", user_id=(None if is_guest else user["username"]))
                        if not is_guest:
                            earned = int(total)
                            try:
                                update_loyalty_points(user["username"], earned)
                                if applied_points > 0:
                                    update_loyalty_points(user["username"], -applied_points)
                            except Exception:
                                st.session_state.loyalty_points = st.session_state.loyalty_points + earned - applied_points
                        st.session_state.notifications.append(f"Order {order_id} placed for pickup {pickup_dt.strftime('%Y-%m-%d %H:%M')}")
                        st.success(f"✅ Order placed! Order ID: {order_id} | Total: ₱{final_total}")
                        st.session_state.cart = {}
                    except Exception as e:
                        st.error(f"Error saving order: {e}")

        with colB:
            st.subheader("✍️ Give Feedback")
            if is_guest:
                st.info("Guests cannot submit feedback. Create an account to leave comments and ratings.")
            else:
                fb_item = st.selectbox("Select Item:", ["(select)"] + [i for cat in menu_data.values() for i in cat.keys()], key="fb_item")
                rating = st.slider("Rate this item (1-5):", 1, 5, 3, key="fb_rating")
                fb_text = st.text_area("Your Feedback:", key="fb_text")
                if st.button("Submit Feedback", key="submit_fb_nonstaff"):
                    if fb_item != "(select)" and fb_text.strip():
                        try:
                            save_feedback(fb_item, fb_text.strip(), rating, username=user["username"])
                            st.success("✅ Feedback submitted!")
                        except Exception as e:
                            st.error(f"Failed to save feedback: {e}")
                    else:
                        st.warning("Choose an item and write feedback.")

            st.markdown("---")
            st.subheader("🔔 Notifications")
            if st.session_state.notifications:
                for n in st.session_state.notifications[-6:]:
                    st.info(n)
            else:
                st.info("No notifications yet.")

        st.divider()
        st.subheader("📦 Order History / Track")
        try:
            receipts_df = load_receipts_df()
            if not receipts_df.empty:
                my = receipts_df if is_guest else receipts_df[receipts_df["user_id"] == user["username"]]
                if not my.empty:
                    st.dataframe(my[["order_id", "items", "total", "payment_method", "pickup_time", "status", "timestamp"]])
                else:
                    st.info("No previous orders found.")
            else:
                st.info("No receipts recorded yet.")
        except Exception as e:
            st.error(f"Could not load receipts: {e}")

        if st.button("Log Out", key="logout_nonstaff"):
            st.session_state.page = "login"
            st.session_state.user = None
            st.rerun()

    # ---------------------------
    # STAFF PORTAL
    # ---------------------------
    elif user["role"] == "Staff":
        st.title("🛠️ BiteHub Staff Portal")
        choice = st.sidebar.radio("Staff Menu", ["Dashboard", "Pending Orders", "Manage Menu", "AI Assistant", "Feedback Review", "Sales Report"])

        if choice == "Dashboard":
            st.subheader("📊 Staff Dashboard")
            st.info("Overview: pending orders, quick sales, and recent feedback.")
            try:
                receipts = load_receipts_df()
                fb = load_feedbacks_df()
                st.metric("Total Orders", len(receipts))
                st.metric("Feedbacks", len(fb))
                pending = receipts[receipts["status"].str.lower() == "pending"] if not receipts.empty else pd.DataFrame()
                st.metric("Pending Orders", len(pending))
            except Exception as e:
                st.error(f"Could not load quick stats: {e}")

        elif choice == "Pending Orders":
            st.subheader("📦 Pending Orders")
            try:
                receipts_df = load_receipts_df()
                if not receipts_df.empty:
                    pending = receipts_df[receipts_df["status"].str.lower() == "pending"]
                    if not pending.empty:
                        for _, row in pending.iterrows():
                            st.write(f"Order {row['order_id']}: {row['items']} — ₱{row['total']} | Pickup: {row['pickup_time']} | By: {row['user_id']}")
                            if st.button(f"Mark Ready {row['order_id']}", key=f"ready_{row['order_id']}"):
                                set_receipt_status(row['order_id'], "Ready for Pickup")
                                st.success(f"Order {row['order_id']} marked ready")
                                st.rerun()
                    else:
                        st.info("No pending orders.")
                else:
                    st.info("No receipts yet.")
            except Exception as e:
                st.error(f"Could not load pending orders: {e}")

        elif choice == "Manage Menu":
            st.subheader("📋 Manage Menu (in-memory demo)")
            cat = st.selectbox("Category", list(menu_data.keys()))
            item = st.text_input("Item name")
            price = st.number_input("Price", min_value=0.0, step=1.0, value=10.0)
            if st.button("Add / Update Item"):
                if item:
                    menu_data[cat][item] = float(price)
                    st.success(f"{item} added/updated in {cat}")
            sel = st.selectbox("Select item to modify", ["(none)"] + [i for c in menu_data.values() for i in c.keys()])
            if sel != "(none)":
                if st.button("Mark Sold Out"):
                    st.session_state.sold_out.add(sel)
                    st.success(f"{sel} marked as Sold Out")
                if st.button("Mark Available"):
                    st.session_state.sold_out.discard(sel)
                    st.success(f"{sel} marked Available")
                if st.button("Remove Item"):
                    for c in menu_data:
                        menu_data[c].pop(sel, None)
                    st.success(f"{sel} removed")

        elif choice == "AI Assistant":
            st.subheader("🤖 Staff AI Assistant")
            staff_q = st.text_input("Ask Staff AI", key="staff_ai_q")
            if st.button("Ask Staff AI", key="staff_ai_btn"):
                try:
                    sales = load_receipts_df().head(50).to_dict()
                    fb = load_feedbacks_df().head(50).to_dict()
                    ctx = f"Sales: {sales}\nFeedback: {fb}"
                except Exception:
                    ctx = "DB context unavailable"
                with st.spinner("Asking AI..."):
                    st.info(run_ai(staff_q, ctx))

        elif choice == "Feedback Review":
            st.subheader("💬 Customer Feedback")
            try:
                fb_df = load_feedbacks_df()
                if not fb_df.empty:
                    st.dataframe(fb_df)
                else:
                    st.info("No feedback yet.")
            except Exception as e:
                st.error(f"Could not load feedbacks: {e}")

        elif choice == "Sales Report":
            st.subheader("📈 Sales Report")
            try:
                receipts_df = load_receipts_df()
                if not receipts_df.empty:
                    st.dataframe(receipts_df)
                    sums = receipts_df.groupby("payment_method")["total"].sum()
                    st.bar_chart(sums)
                else:
                    st.info("No sales yet.")
            except Exception as e:
                st.error(f"Could not load sales: {e}")

        if st.button("Log Out", key="logout_staff"):
            st.session_state.page = "login"
            st.session_state.user = None
            st.rerun()
