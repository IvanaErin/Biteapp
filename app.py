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

import os, base64, hashlib, secrets, re
import streamlit as st
import pandas as pd
from PIL import Image
from groq import Groq

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

# ---------------------------
# MENU DATA
# ---------------------------
menu_df = pd.read_csv("menu.csv")
menu_data = {}
for cat, group in menu_df.groupby("Category"):
    menu_data[cat] = dict(zip(group["Item"], group["Price"]))

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
# LOGIN PAGE
# ---------------------------
if st.session_state.page == "login":
    logo = Image.open("bite.jpg")
    logo = logo.resize((350, 150))

    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        st.image(logo, use_container_width=False)

    username = st.text_input("Username", placeholder="Enter username", key="login_username")
    password = st.text_input("Password", type="password", placeholder="Enter password", key="login_password")

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
                st.error("❌ Invalid username or password.")
    with col3:
        if st.button("Guest Account", use_container_width=True):
            st.session_state.user = {"username": "Guest", "role": "Non-Staff", "loyalty_points": 0}
            st.session_state.page = "main"
            st.rerun()
    with col4:
        if st.button("Create Account", use_container_width=True):
            st.session_state.page = "signup"
            st.rerun()

# ---------------------------
# SIGNUP PAGE
# ---------------------------
elif st.session_state.page == "signup":
    st.markdown('<div class="login-card">', unsafe_allow_html=True)
    st.markdown("<h2>✍️ Create Account</h2>", unsafe_allow_html=True)

    new_username = st.text_input("New Username", key="signup_username")
    new_pass = st.text_input("New Password", type="password", key="signup_password")
    new_role = st.selectbox("Role", ["Non-Staff", "Staff"], key="signup_role")

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
                if get_account(new_username):
                    st.error("Username already exists.")
                else:
                    save_account(new_username, new_pass, new_role)
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
# HELPER: Get current user safely
# ---------------------------
def get_current_user():
    user = st.session_state.get("user")
    if not isinstance(user, dict):
        # Fallback to guest
        user = {"username": "Guest", "role": "Non-Staff", "loyalty_points": 0}
    # Ensure required keys exist
    user.setdefault("username", "Guest")
    user.setdefault("role", "Non-Staff")
    user.setdefault("loyalty_points", 0)
    return user

# ---------------------------
# SESSION VARIABLES
# ---------------------------
user = get_current_user()
is_guest = user.get("username") == "Guest"
page = st.session_state.get("page", "main")

# ---------------------------
# MAIN PORTAL
# ---------------------------
if page == "main":
    st.title(f"🏫 Welcome {user['username']} to BiteHub")
    
    if is_guest:
        st.warning(
            "🔓 You're on a Guest session. Create an account to enjoy loyalty points, promos, and feedback posting."
        )
        st.info("Your cart is empty. Add items from the menu to order.")

# ---------------------------
# NON-STAFF UX
# ---------------------------
if user.get("role") == "Non-Staff":
    col_left, col_right = st.columns([1, 1])
    # RIGHT: Feedback / Sentiment
    with col_right:
        st.subheader("📝 Feedback Sentiment Analysis")
        for idx, (item_name, qty) in enumerate(st.session_state.cart.items()):
            fb_key = f"feedback_{idx}_{item_name}"  # unique key
            feedback_text = st.text_area(f"Your feedback for {item_name}:", key=fb_key)
            analyze_key = f"analyze_{idx}_{item_name}"
            if feedback_text and st.button(f"Analyze Sentiment for {item_name}", key=analyze_key):
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

    # ---------------------------
    # Notifications
    # ---------------------------
    if "notifications" in st.session_state and st.session_state.notifications:
        st.subheader("📢 Notifications")
        for note in st.session_state.notifications:
            st.info(note)
        if st.button("Clear notifications", key="clear_notifs"):
            st.session_state.notifications.clear()

    # ---------------------------
    # Menu display & Feedback
    # ---------------------------
    st.divider()
    st.subheader("📋 Full Menu")
    colA, colB = st.columns([2, 1])

    with colA:
        for cat, items in menu_data.items():
            with st.expander(cat, expanded=False):
                for item_name, price in items.items():
                    add_key = f"add_{cat}_{item_name}".replace(" ", "_")
                    if st.button(f"Add {item_name}", key=add_key):
                        st.session_state.cart[item_name] = st.session_state.cart.get(item_name, 0) + 1
                        st.success(f"Added 1 x {item_name}")
                        st.rerun()

    with colB:
        st.subheader("✍️ Give Feedback")
        if is_guest:
            st.info("Guests cannot submit feedback. Create an account to leave comments and ratings.")
        else:
            fb_item = st.selectbox(
                "Select Item:", 
                ["(select)"] + [i for cat in menu_data.values() for i in cat.keys()], 
                key="fb_item"
            )
            rating = st.slider("Rate this item (1-5):", 1, 5, 3, key="fb_rating")
            fb_text = st.text_area("Your Feedback:", key="fb_text")
            if st.button("Submit Feedback", key="submit_fb_nonstaff"):
                if fb_item != "(select)" and fb_text.strip():
                    try:
                        save_feedback(fb_item, fb_text.strip(), rating, user_id=user["username"])
                        st.success("✅ Feedback submitted!")
                    except Exception as e:
                        st.error(f"Failed to save feedback: {e}")
                else:
                    st.warning("Choose an item and write feedback.")

    # ---------------------------
    # Cart & Checkout
    # ---------------------------
    st.divider()
    st.subheader("🛒 Your Cart")

    if st.session_state.cart:
        total = 0
        for item, qty in list(st.session_state.cart.items()):
            price = menu_data[next(cat for cat in menu_data if item in menu_data[cat])][item]
            col1, col2 = st.columns([3, 1])
            with col1:
                st.write(f"{item} x {qty} — ₱{price * qty}")
            with col2:
                if st.button(f"➖ Remove {item}", key=f"remove_{item}"):
                    if st.session_state.cart[item] > 1:
                        st.session_state.cart[item] -= 1
                    else:
                        del st.session_state.cart[item]
                    st.rerun()
            total += price * qty

        st.write(f"**Subtotal: ₱{total}**")

        pickup_date = st.date_input("Pickup date (optional)", value=date.today(), key="pickup_date")
        pickup_time = st.time_input("Pickup time (optional)", value=datetime.now().time(), key="pickup_time")

        payment_method = st.radio("Select Payment Method:", ["Cash", "GCash", "Card"], key="pay_method")

        if st.button("Checkout", key="checkout_btn"):
            order_id = f"ORD{random.randint(10000,99999)}"
            items_str = ", ".join([f"{i} x{q}" for i,q in st.session_state.cart.items()])
            user_id = user["username"]
            pickup_dt = datetime.combine(pickup_date, pickup_time) if pickup_date and pickup_time else None

            try:
                save_receipt(order_id, items_str, total, payment_method=payment_method, 
                             user_id=user_id, pickup_time=pickup_dt, status="Pending")

                if "notifications" not in st.session_state:
                    st.session_state.notifications = []
                st.session_state.notifications.append(
                    f"Order {order_id} placed — Payment: {payment_method} — Pickup: {pickup_dt.strftime('%Y-%m-%d %H:%M') if pickup_dt else 'ASAP'}"
                )

                if not is_guest:
                    try:
                        new_points = update_loyalty_points(user["username"], total // 100)
                        st.session_state.loyalty_points = new_points
                    except Exception:
                        st.session_state.loyalty_points += total // 100

                st.success(f"✅ Order placed! Order ID: {order_id}")
                with st.expander("🧾 View Receipt", expanded=True):
                    st.write(f"**Order ID:** {order_id}")
                    st.write(f"**User:** {user_id}")
                    st.write(f"**Items:** {items_str}")
                    st.write(f"**Total:** ₱{total}")
                    st.write(f"**Payment Method:** {payment_method}")
                    if pickup_dt:
                        st.write(f"**Pickup:** {pickup_dt.strftime('%Y-%m-%d %H:%M')}")
                    st.write(f"**Status:** Pending")

                    receipt_text = (
                        f"Order ID: {order_id}\nUser: {user_id}\nItems: {items_str}\n"
                        f"Total: ₱{total}\nPayment Method: {payment_method}\n"
                        f"Pickup: {pickup_dt if pickup_dt else 'ASAP'}\nStatus: Pending\n"
                        f"Placed at: {datetime.now().isoformat()}\n"
                    )
                    st.download_button("Download Receipt (txt)", data=receipt_text, file_name=f"{order_id}_receipt.txt")

                st.session_state.cart.clear()
                st.experimental_rerun()

            except Exception as e:
                st.error(f"Failed to place order: {e}")

    else:
        st.info("Your cart is empty. Add items from the menu to order.")

    # ---------------------------
    # Order History
    # ---------------------------
    st.divider()
    st.subheader("📜 Your Order History")
    try:
        history = load_receipts_df()
        if not history.empty:
            user_orders = history[history["user_id"] == user["username"]]
            if not user_orders.empty:
                st.dataframe(user_orders.sort_values(by="timestamp", ascending=False), use_container_width=True)
            else:
                st.info("No past orders yet.")
        else:
            st.info("No orders have been made yet.")
    except Exception as e:
        st.error(f"Could not load order history: {e}")


# ---------------------------
# STAFF UX
# ---------------------------
elif user["role"] == "Staff":
    st.title("🛠️ BiteHub Staff Portal")
    choice = st.sidebar.radio(
        "Staff Menu", 
        ["Dashboard", "Pending Orders", "Manage Menu", "AI Assistant", "Feedback Review", "Sales Report"]
    )

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
                        btn_key = f"ready_{row['order_id']}"
                        st.write(
                            f"Order {row['order_id']}: {row['items']} — ₱{row['total']} "
                            f"| By: {row['user_id']} | Status: {row['status']}"
                        )
                        if st.button(f"Mark Ready {row['order_id']}", key=btn_key):
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
        st.subheader("📖 Manage Menu")
        st.info("Add new items or update prices here.")
        menu_edit_df = pd.DataFrame([
            {"Category": cat, "Item": item, "Price": price}
            for cat, items in menu_data.items()
            for item, price in items.items()
        ])
        edited_df = st.data_editor(menu_edit_df, num_rows="dynamic", use_container_width=True)

        if st.button("💾 Save Menu Updates"):
            try:
                edited_df.to_csv("menu.csv", index=False)
                st.success("✅ Menu updated successfully!")
                new_menu = {}
                for _, row in edited_df.iterrows():
                    if row["Category"] not in new_menu:
                        new_menu[row["Category"]] = {}
                    new_menu[row["Category"]][row["Item"]] = row["Price"]
                menu_data.clear()
                menu_data.update(new_menu)
                st.rerun()
            except Exception as e:
                st.error(f"Failed to update menu: {e}")

    elif choice == "AI Assistant":
        st.subheader("🤖 Staff AI Assistant")
        q = st.text_input("Ask AI about sales, menu trends, or customer feedback:")
        if st.button("Ask Staff AI"):
            if q:
                with st.spinner("Thinking..."):
                    answer = run_ai(q, extra_context="STAFF MODE: Provide analytics insights if possible.")
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
            receipts["date"] = pd.to_datetime(receipts.get("timestamp", datetime.now())).dt.date
            daily_sales = receipts.groupby("date")["total"].sum().reset_index()

            st.line_chart(daily_sales.set_index("date"))

            st.metric("Total Sales", f"₱{receipts['total'].sum():,.2f}")
            st.metric("Total Orders", len(receipts))
        else:
            st.info("No sales data available yet.")

    if st.button("Log Out", key="logout_staff"):
        st.session_state.page = "login"
        st.session_state.user = None
        st.rerun()
