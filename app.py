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
from textblob import TextBlob


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
# SENTIMENT ANALYSIS
# ---------------------------
def analyze_sentiment(feedback: str):
    """
    Analyze text using TextBlob and return sentiment label + polarity score.
    """
    blob = TextBlob(feedback)
    polarity = blob.sentiment.polarity  # -1 (negative) → +1 (positive)
    if polarity > 0.1:
        sentiment = "Positive"
    elif polarity < -0.1:
        sentiment = "Negative"
    else:
        sentiment = "Neutral"
    return sentiment, polarity


# ---------------------------
# FEEDBACK FUNCTIONS
# ---------------------------
def save_feedback(item: str, feedback: str, rating: int, user_id: str):
    sentiment, polarity = analyze_sentiment(feedback)

    conn = get_connection()
    if not conn:
        _ensure_local_db()
        st.session_state._local_feedbacks.append({
            "item": item,
            "feedback": feedback,
            "rating": rating,
            "user_id": user_id,
            "sentiment": sentiment,
            "polarity": polarity,
            "timestamp": datetime.now()
        })
        return
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO feedbacks (item, feedback, rating, user_id, sentiment, polarity) VALUES (%s, %s, %s, %s, %s, %s)",
            (item, feedback, rating, user_id, sentiment, polarity)
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
        return pd.DataFrame(rows) if rows else pd.DataFrame(
            columns=["item","feedback","rating","user_id","sentiment","polarity","timestamp"]
        )
    try:
        cur = conn.cursor()
        cur.execute("SELECT item, feedback, rating, user_id, sentiment, polarity, timestamp FROM feedbacks ORDER BY timestamp DESC")
        rows = cur.fetchall()
        return pd.DataFrame(rows, columns=["item","feedback","rating","user_id","sentiment","polarity","timestamp"])
    finally:
        cur.close()
        conn.close()


# ---------------------------
# MENU FUNCTIONS (with sentiment)
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

        # --- integrate sentiment ---
        feedbacks = load_feedbacks_df()
        if not feedbacks.empty:
            sentiment_summary = (
                feedbacks.groupby("item")["sentiment"]
                .value_counts()
                .unstack(fill_value=0)
                .reset_index()
            )
            df = df.merge(sentiment_summary, how="left", left_on="ITEM", right_on="item").fillna(0)
        else:
            df["Positive"] = 0
            df["Neutral"] = 0
            df["Negative"] = 0

        return df

    except Exception as e:
        print(f"❌ Error loading menu with sentiment: {e}")
        return pd.DataFrame(columns=["CATEGORY", "ITEM", "PRICE"])
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


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
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


# ---------------------------
# MENU + SENTIMENT DISPLAY
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

        # --- integrate sentiment ---
        feedbacks = load_feedbacks_df()
        if not feedbacks.empty:
            sentiment_summary = (
                feedbacks.groupby("item")["sentiment"]
                .value_counts()
                .unstack(fill_value=0)
                .reset_index()
            )
            df = df.merge(sentiment_summary, how="left", left_on="ITEM", right_on="item").fillna(0)
        else:
            df["Positive"] = 0
            df["Neutral"] = 0
            df["Negative"] = 0

        return df

    except Exception as e:
        print(f"❌ Error loading menu with sentiment: {e}")
        return pd.DataFrame(columns=["CATEGORY", "ITEM", "PRICE"])
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


def detect_menu_columns(menu_df):
    """
    Automatically detects which columns in the menu DataFrame 
    correspond to category, item, and price.
    """
    cols_lower = [c.lower() for c in menu_df.columns]

    cat_col = next((c for c in menu_df.columns if "category" in c.lower()), menu_df.columns[0])
    item_col = next((c for c in menu_df.columns if "item" in c.lower()), menu_df.columns[1])
    price_col = next((c for c in menu_df.columns if "price" in c.lower()), menu_df.columns[2])

    return cat_col, item_col, price_col


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
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


# ---------------------------
# MENU + SENTIMENT DISPLAY
# ---------------------------
def display_menu_with_sentiment():
    st.subheader("🍽️ Menu with Sentiment Insights")

    menu_df = load_menu()
    if menu_df.empty:
        st.info("No menu items available.")
        return

    for _, row in menu_df.iterrows():
        item = row["ITEM"]
        st.markdown(f"### {item} — ₱{row['PRICE']:.2f}")

        # Extract sentiment counts (already merged)
        pos = int(row.get("Positive", 0))
        neu = int(row.get("Neutral", 0))
        neg = int(row.get("Negative", 0))
        total = pos + neu + neg

        if total > 0:
            st.progress(pos / total)
            st.caption(f"😊 Positive: {pos} | 😐 Neutral: {neu} | 😞 Negative: {neg}")

            # Optional mini pie chart
            labels = ["Positive", "Neutral", "Negative"]
            values = [pos, neu, neg]
            fig, ax = plt.subplots(figsize=(3, 3))
            ax.pie(values, labels=labels, autopct="%1.1f%%", startangle=90)
            ax.axis("equal")
            st.pyplot(fig)
        else:
            st.caption("No feedback yet for this item.")
            
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
            
            # 1️⃣ Check if hardcoded staff
            if username == "staff1" and password == "staff123":
                st.session_state.user = {"username": "staff1", "role": "Staff", "loyalty_points": 0}
                st.session_state.page = "main"
                st.success(f"✅ Welcome Staff {username}!")
                st.rerun()
            
            # 2️⃣ Check database users
            else:
                acc = get_account(username)
                if acc and verify_password(acc["password"], password):
                    role_value = acc.get("role", "Non-Staff")
                    try:
                        normalized_role = str(role_value).strip().capitalize()
                    except Exception:
                        normalized_role = "Non-Staff"
                    acc["role"] = normalized_role
                    st.session_state.user = acc

                    if normalized_role == "Staff":
                        st.session_state.page = "staff_dashboard"
                        st.success(f"✅ Welcome Staff {acc['username']}!")
                    else:
                        st.session_state.page = "main"
                        st.success(f"✅ Welcome {acc['username']}!")
                    st.rerun()
                else:
                    st.error("❌ Invalid username or password.")

    with col3:
        if st.button("🎟️ Guest Account", use_container_width=True):
            st.session_state.user = {"username": "Guest", "role": "Non-Staff", "loyalty_points": 0, "cart": []}
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

    # --- live validation ---
    rules = password_valid_rules(new_password)
    st.markdown("*Password rules:* (all must be ✅ to register)")
    st.write(f"- Minimum 12 chars: {'✅' if rules['length'] else '❌'}")
    st.write(f"- Uppercase letter: {'✅' if rules['upper'] else '❌'}")
    st.write(f"- Lowercase letter: {'✅' if rules['lower'] else '❌'}")
    st.write(f"- Number: {'✅' if rules['digit'] else '❌'}")
    st.write(f"- Symbol: {'✅' if rules['symbol'] else '❌'}")

    if st.button("✅ Register Account"):
        if not new_username or not new_password:
            st.warning("⚠️ Please fill in all fields.")
        elif new_password != confirm_password:
            st.warning("⚠️ Passwords do not match.")
        elif not all(rules.values()):
            st.warning("⚠️ Password does not meet requirements.")
        elif get_account(new_username):
            st.error("⚠️ Username already exists.")
        else:
            # --- HASH PASSWORD BEFORE SAVING ---
            hashed_pass = hash_password(new_password)
            save_account(new_username, hashed_pass, role="Non-Staff")
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
            st.markdown("<h2 style='color:#FF6F61;'>📦 Pending Orders</h2>", unsafe_allow_html=True)

            # 🔄 Manual refresh button
            if st.button("🔄 Refresh Orders"):
                st.session_state["last_refresh"] = datetime.now()
                st.rerun()

            # 🔁 Auto-refresh every 10 seconds
            refresh_interval = 10  # seconds
            last_refresh = st.session_state.get("last_refresh", datetime.now())
            if (datetime.now() - last_refresh).seconds >= refresh_interval:
                st.session_state["last_refresh"] = datetime.now()
                st.rerun()

            # --- Load orders from Snowflake ---
            receipts = load_receipts_df()

            # --- Load local guest receipts ---
            local = st.session_state.get("_local_receipts", [])
            if local:
                local_df = pd.DataFrame(local)

                # Rename pickup_dt → pickup_time for consistency
                if "pickup_dt" in local_df.columns:
                    local_df = local_df.rename(columns={"pickup_dt": "pickup_time"})

                # Ensure all required columns exist
                required_cols = ["order_id","items","total","payment_method","user_id","pickup_time","status","timestamp"]
                for col in required_cols:
                    if col not in local_df.columns:
                        local_df[col] = None

                # Merge local with DB receipts
                if receipts is None or receipts.empty:
                    receipts = local_df
                else:
                    receipts = pd.concat([receipts, local_df], ignore_index=True)

            # --- Normalize guest user_id ---
            if receipts is not None and not receipts.empty:
                receipts["user_id"] = receipts["user_id"].fillna("Guest").astype(str).str.strip()

            # --- Filter pending ---
            if receipts is not None and not receipts.empty:
                pending_orders = receipts[receipts["status"].astype(str).str.lower() == "pending"]
            else:
                pending_orders = pd.DataFrame()

            # --- Display styled cards ---
            if not pending_orders.empty:
                for idx, row in pending_orders.iterrows():
                    order_id = row.get("order_id")
                    user_id = row.get("user_id") or "Guest"
                    payment = row.get("payment_method", "N/A")
                    total = row.get("total", 0)
                    pickup_time = row.get("pickup_time", "N/A")

                    st.markdown(
                        f"""
                        <div style="
                            background-color: #fff;
                            border: 2px solid #FF6F61;
                            border-radius: 15px;
                            padding: 15px 20px;
                            margin-bottom: 15px;
                            box-shadow: 2px 2px 8px rgba(0,0,0,0.1);
                            color: #000000;  /* All text black */
                        ">
                            <h4 style='color:#FF6F61;'>Order #{order_id}</h4>
                            <p><b>User:</b> {user_id}</p>
                            <p><b>Payment:</b> {payment}</p>
                            <p><b>Total:</b> ₱{total:.2f}</p>
                            <p><b>Pickup Time:</b> {pickup_time}</p>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    # 🧾 Show items cleanly
                    items = row.get("items")
                    if isinstance(items, str):
                        try:
                            items = json.loads(items)
                        except Exception:
                            pass

                    if isinstance(items, list):
                        for i in items:
                            st.markdown(f"<span style='color:black;'>- {i.get('name', '')} — Qty: {i.get('qty', 1)} @ ₱{i.get('price', 0)}</span>", unsafe_allow_html=True)
                    elif isinstance(items, dict):
                        for name, details in items.items():
                            st.markdown(f"<span style='color:black;'>- {name} — Qty: {details.get('qty', 1)} @ ₱{details.get('price', 0)}</span>", unsafe_allow_html=True)

                    # ✅ Mark ready button
                    if st.button("✅ Mark as Ready", key=f"ready_{order_id}"):
                        update_order_status(order_id, "Ready")

                        # --- Send notification to user (guest or non-staff) ---
                        notify_user_id = row.get("user_id") or "Guest"
                        add_notification(notify_user_id, f"Your order #{order_id} is ready for pickup!")

                        st.success(f"Order #{order_id} marked as Ready!")
                        st.rerun()

                    st.divider()
            else:
                st.info("No pending orders found.")

        elif choice == "Manage Menu":
            st.subheader("📖 Manage Menu")
            menu_df = load_menu()

            if not menu_df.empty:
                menu_df["Delete"] = False
                edited = st.data_editor(menu_df, num_rows="dynamic")

                # Save updates
                if st.button("💾 Save Menu Updates"):
                    upsert_menu(edited.drop(columns=["Delete"]))
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

            if not fb.empty:
                st.dataframe(fb, use_container_width=True)
            else:
                st.info("No feedbacks yet.")

        elif choice == "Sales Report":
            st.subheader("💰 Sales Breakdown")
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
                                "ITEM_NAME": it.get("name", "Unknown"),
                                "QUANTITY": int(it.get("qty", 1))
                            })
                    except Exception:
                        pass
                if all_items:
                    sales_summary = pd.DataFrame(all_items).groupby("ITEM_NAME", as_index=False).sum()

                    fig, ax = plt.subplots(figsize=(5, 5))
                    wedges, texts, autotexts = ax.pie(
                        sales_summary["QUANTITY"],
                        labels=sales_summary["ITEM_NAME"],
                        autopct="%1.1f%%",
                        startangle=90,
                        pctdistance=0.8,   # move percentages further out
                        labeldistance=1.1  # move labels further from center
                    )
                    
                    # Make lines connecting labels to slices
                    for autotext in autotexts:
                        autotext.set_color('black')
                        autotext.set_fontsize(8)

                    ax.axis("equal")  # keep pie circular
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
                    menu_df = load_menu()
                    menu_list = "\n".join([f"{row['CATEGORY']} - {row['ITEM']} (₱{row['PRICE']})" for _, row in menu_df.iterrows()])
                    prompt = f"""
                    You are BiteHub's AI assistant. Only use items from the menu below.
                    Prices are in Pesos (₱). Do NOT invent items or prices.

                    MENU:
                    {menu_list}

                    USER QUESTION: {q}
                    """
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

            # 🛒 CART SECTION
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

        # RIGHT SIDE
        with right_col:
            st.subheader("💬 Sentiment Analysis")
            feedback_df = load_feedbacks_df()

            if feedback_df.empty:
                st.info("No feedback data available yet.")
            else:
                avg_sentiment = feedback_df.groupby("item")["rating"].mean().reset_index()
                avg_sentiment.columns = ["Item", "Average Rating"]

                selected_item = st.selectbox("Select item to view sentiment:", avg_sentiment["Item"].unique())

                if selected_item:
                    avg_score = avg_sentiment.loc[avg_sentiment["Item"] == selected_item, "Average Rating"].values[0]
                    st.metric(label=f"Sentiment for {selected_item}", value=f"{avg_score:.2f} ⭐")

                    if avg_score >= 4:
                        st.success("😊 Customers love this item!")
                    elif avg_score >= 3:
                        st.warning("😐 Mixed feedback from customers.")
                    else:
                        st.error("😞 Needs improvement based on reviews.")

            st.divider()
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

            # Use "Guest" if user is not logged in
            user_id = user.get("username") if user.get("username") else "Guest"

            # 🔄 Manual refresh button
            if st.button("🔄 Refresh Notifications"):
                st.session_state["last_notif_refresh"] = datetime.now()
                st.rerun()

            # 🔁 Auto-refresh every 10 seconds
            notif_refresh_interval = 10  # seconds
            last_notif_refresh = st.session_state.get("last_notif_refresh", datetime.now())
            if (datetime.now() - last_notif_refresh).seconds >= notif_refresh_interval:
                st.session_state["last_notif_refresh"] = datetime.now()
                st.rerun()

            notes = get_notifications_for_user(user_id)
            if notes:
                for n in notes:
                    st.info(n)
            else:
                st.info("No notifications yet.")

            if st.button("Clear"):
                clear_notifications_for_user(user_id)
                st.success("Cleared.")

            st.divider()
            st.subheader("📜 Order History")

            # --- Manual Refresh Button ---
            if st.button("🔄 Refresh History"):
                st.session_state["last_history_refresh"] = datetime.now()
                st.rerun()

            # --- Auto-Refresh Every 15 Seconds ---
            refresh_interval = 15  # seconds
            last_refresh = st.session_state.get("last_history_refresh", datetime.now())
            if (datetime.now() - last_refresh).seconds >= refresh_interval:
                st.session_state["last_history_refresh"] = datetime.now()
                st.rerun()

            # --- Guest Check ---
            if user.get("username", "").strip().lower() == "guest":
                st.warning("⚠️ Orders can't be saved. Please log in to view your order history.")
            else:
                # --- Load Order History ---
                hist = load_receipts_df()
                if not hist.empty:
                    u_orders = hist[hist["user_id"] == user["username"]]
                    if not u_orders.empty:
                        # --- Fix columns for PyArrow ---
                        u_orders["timestamp"] = pd.to_datetime(u_orders["timestamp"], errors="coerce")
                        for col in u_orders.columns:
                            if u_orders[col].dtype == "object":
                                u_orders[col] = u_orders[col].astype(str)
                        st.dataframe(u_orders.sort_values(by="timestamp", ascending=False))
                    else:
                        st.info("No orders yet.")
                else:
                    st.info("No receipts found.")

        st.divider()
        if st.button("🚪 Log Out"):
            keys_to_keep = ["page"]
            for k in list(st.session_state.keys()):
                if k not in keys_to_keep:
                    del st.session_state[k]
            st.session_state["page"] = "login"
            st.rerun()

# ---------------------------
# PAYMENT PAGE
# ---------------------------
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

        def record_order(payment_method):
            """Record order for both logged-in users and guests"""
            order_id = f"ORD-{random.randint(100000,999999)}"
            # Determine user_id
            user_id = user.get("username") if user.get("role") != "Guest" else "Guest"

            # Save receipt to DB and local session
            save_receipt(order_id, pending_cart, total_cost, payment_method,
                         user_id, pickup_dt, status="Pending")

            # Add notification for the user
            add_notification(user_id, f"Your order #{order_id} has been placed successfully!")

            # Show success message
            st.success(f"✅ Order #{order_id} recorded (Pending). Staff will mark it Ready when prepared.")

            # Clear cart and return to main
            st.session_state.cart.clear()
            st.session_state.page = "main"
            st.rerun()

        if method == "Cash":
            if st.button("Confirm Cash Payment"):
                record_order("Cash")

        elif method == "GCash (Scan QR)":
            st.info("📱 Please scan the QR code below using your GCash app to pay.")
            st.image("Qr.jpg", caption="Scan this QR to pay via GCash", width=250)
            st.markdown(f"**Amount to pay:** ₱{total_cost:.2f}")

            if st.button("✅ I've Paid via GCash"):
                record_order("GCash")

        elif method == "Card":
            st.text_input("Card Number", key="card_num")
            st.text_input("Expiry (MM/YY)", key="card_exp")
            st.text_input("CVV", key="card_cvv")
            if st.button("Simulate Card Payment Success"):
                record_order("Card")
