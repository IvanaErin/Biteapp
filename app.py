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
import math
import requests
from io import BytesIO

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

def set_background(image_file: str | None = None, color: str = "#f0f0f0"):
    """
    Set a background image (if provided), otherwise a solid background color.
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
    else:
        css_parts.append(
            f"""
            [data-testid="stAppViewContainer"] {{
                background-color: {color};
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
        """
    )

    st.markdown("<style>" + "\n".join(css_parts) + "</style>", unsafe_allow_html=True)

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
# Validate image URLs
# ---------------------------
def validate_image_url(url):
    """
    Returns a valid image URL for Streamlit.
    If the URL is None, empty, or whitespace, returns a placeholder.
    """
    placeholder = "https://via.placeholder.com/150"
    if url is None or not str(url).strip():
        return placeholder
    return url

# ---------------------------
# Load menu safely
# ---------------------------
def load_menu():
    conn = get_connection()
    if not conn:
        return pd.DataFrame(columns=["CATEGORY", "ITEM", "PRICE", "IMAGE_URL", "VALID_IMAGE"])

    try:
        cur = conn.cursor()
        cur.execute("SELECT CATEGORY, ITEM, PRICE, IMAGE_URL FROM MENU ORDER BY CATEGORY, ITEM")
        df = cur.fetch_pandas_all()
        df["PRICE"] = pd.to_numeric(df.get("PRICE", 0), errors="coerce").fillna(0)

        # Validate images
        df["VALID_IMAGE"] = df["IMAGE_URL"].apply(validate_image_url)

        return df

    except Exception as e:
        print(f"❌ Error loading menu: {e}")
        return pd.DataFrame(columns=["CATEGORY", "ITEM", "PRICE", "IMAGE_URL", "VALID_IMAGE"])
    finally:
        try:
            cur.close()
            conn.close()
        except:
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

    # --- Dropdown to choose which item to view ---
    selected_item = st.selectbox("Select an item to view sentiment:", menu_df["ITEM"].unique())

    # --- Load feedbacks separately for accurate sentiment mapping ---
    feedbacks = load_feedbacks_df()
    item_feedbacks = feedbacks[feedbacks["item"] == selected_item] if not feedbacks.empty else pd.DataFrame()

    # --- Show menu item details ---
    row = menu_df[menu_df["ITEM"] == selected_item].iloc[0]
    st.markdown(f"### {selected_item} — ₱{row['PRICE']:.2f}")

    if not item_feedbacks.empty:
        # Count sentiment distribution
        sentiment_counts = item_feedbacks["sentiment"].value_counts().to_dict()
        pos = sentiment_counts.get("Positive", 0)
        neu = sentiment_counts.get("Neutral", 0)
        neg = sentiment_counts.get("Negative", 0)
        total = pos + neu + neg

        if total > 0:
            st.progress(pos / total)
            st.caption(f"😊 Positive: {pos} | 😐 Neutral: {neu} | 😞 Negative: {neg}")

            # Mini pie chart
            labels = ["Positive", "Neutral", "Negative"]
            values = [pos, neu, neg]
            fig, ax = plt.subplots(figsize=(3, 3))
            wedges, texts, autotexts = ax.pie(
                values,
                labels=labels,
                autopct=lambda p: f"{p:.1f}%" if p > 0 else "",
                startangle=90,
                textprops={"fontsize": 9}
            )
            ax.axis("equal")
            st.pyplot(fig)
        else:
            st.caption("No feedbacks yet for this item.")
    else:
        st.caption("No feedbacks yet for this item.")

# ---------------------------
# NOTIFICATION FUNCTIONS
# ---------------------------

def add_notification(user_id, message):
    """Save a notification for the user."""
    conn = get_connection()
    if not conn:
        print("⚠️ No DB connection for add_notification.")
        return

    try:
        cur = conn.cursor()
        notif_id = str(uuid.uuid4())  # Generate unique ID
        cur.execute("""
            INSERT INTO notifications (NOTIF_ID, USER_ID, MESSAGE, STATUS, CREATED_AT)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
        """, (notif_id, user_id, message, "Unread"))
        conn.commit()
        print(f"✅ Notification saved for {user_id}: {message}")
    except Exception as e:
        print(f"❌ Error saving notification: {e}")
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass

def save_notification(user_id: str, message: str):
    """Save a new notification for a specific user."""
    conn = get_connection()
    if not conn:
        # --- Local fallback ---
        if "_local_notifications" not in st.session_state:
            st.session_state._local_notifications = []
        st.session_state._local_notifications.append({
            "user_id": user_id,
            "message": message,
            "timestamp": datetime.now()
        })
        return

    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO notifications (user_id, message, timestamp) VALUES (%s, %s, %s)",
            (user_id, message, datetime.now())
        )
        conn.commit()
    except Exception as e:
        print(f"❌ Error saving notification: {e}")
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


def get_notifications_for_user(user_id: str):
    """Retrieve notifications for a specific user."""
    conn = get_connection()
    if not conn:
        # --- Local fallback ---
        if "_local_notifications" in st.session_state:
            notes = [n["message"] for n in st.session_state._local_notifications if n["user_id"] == user_id]
            return sorted(notes, reverse=True)
        return []

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT MESSAGE 
            FROM notifications 
            WHERE USER_ID = %s 
            ORDER BY CREATED_AT DESC
        """, (user_id,))
        rows = cur.fetchall()
        return [r[0] for r in rows] if rows else []
    except Exception as e:
        print(f"❌ Error loading notifications: {e}")
        return []
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass

def clear_notifications_for_user(user_id: str):
    """Clear all notifications for a specific user."""
    conn = get_connection()
    if not conn:
        if "_local_notifications" in st.session_state:
            st.session_state._local_notifications = [
                n for n in st.session_state._local_notifications
                if n["user_id"].strip().lower() != user_id.strip().lower()
            ]
        return

    try:
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM notifications 
            WHERE LOWER(TRIM(user_id)) = LOWER(TRIM(%s))
        """, (user_id,))
        conn.commit()
        print(f"✅ Notifications cleared for user: {user_id}")
    except Exception as e:
        print(f"❌ Error clearing notifications: {e}")
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass

def get_all_non_staff_users():
    """
    Fetch all usernames of Non-Staff and Guest users from the USERS table.
    Used for sending notifications about new menu items.
    """
    conn = get_connection()
    try:
        query = """
            SELECT USERNAME
            FROM USERS
            WHERE ROLE != 'Staff'
        """
        cur = conn.cursor()
        cur.execute(query)
        result = [row[0] for row in cur.fetchall()]
        return result
    finally:
        conn.close()

def update_order_status(order_id, new_status):
    """Update the order status in the receipts table."""
    conn = get_connection()
    if not conn:
        print("⚠️ No DB connection for update_order_status.")
        return False

    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE receipts SET status = %s WHERE order_id = %s",
            (new_status, order_id)
        )
        conn.commit()
        print(f"✅ Order {order_id} marked as {new_status}.")
        return True
    except Exception as e:
        print(f"❌ Error updating order status: {e}")
        return False
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass

def save_receipt(order_id, cart, total, payment_method, user_id, pickup_time, status="Pending"):
    """Save the order (receipt) into Snowflake."""
    conn = get_connection()
    if not conn:
        print("⚠️ No DB connection for save_receipt.")
        return

    try:
        cur = conn.cursor()
        items_json = json.dumps(cart, ensure_ascii=False)

        sql = """
            INSERT INTO receipts (ORDER_ID, USER_ID, ITEMS, TOTAL, PAYMENT_METHOD, PICKUP_TIME, STATUS, TIMESTAMP)
            VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        """
        cur.execute(sql, (
            order_id,
            user_id,
            items_json,
            float(total),
            payment_method,
            pickup_time,
            status
        ))
        conn.commit()
        print(f"✅ Order saved: {order_id} for user {user_id}")

        # 🔔 Notify user
        add_notification(
            user_id,
            f"🎉 Your order #{order_id} has been placed successfully! We'll notify you once it's ready for pickup."
        )

    except Exception as e:
        print(f"❌ Error saving receipt: {e}")
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass
            
def load_receipts_df():
    """Load all receipts from Snowflake."""
    conn = get_connection()
    if not conn:
        print("⚠️ No DB connection for load_receipts_df.")
        return pd.DataFrame()

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT ORDER_ID, USER_ID, ITEMS, TOTAL, PAYMENT_METHOD, PICKUP_TIME, STATUS, TIMESTAMP
            FROM receipts
            ORDER BY TIMESTAMP DESC
        """)
        rows = cur.fetchall()
        cols = [desc[0].lower() for desc in cur.description]
        return pd.DataFrame(rows, columns=cols)
    except Exception as e:
        print(f"❌ Error loading receipts: {e}")
        return pd.DataFrame()
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass
            
# ---------------------------
# AI
# ---------------------------
def run_ai_staff(question):
    system_prompt = """
    You are BiteHub's Staff Assistant.
    Your role is to help canteen staff manage operations, such as:
    - Updating menu items, prices, and categories.
    - Managing orders, stocks, and staff-related tasks.
    - Explaining errors in simple, clear terms.
    - Suggesting practical improvements.
    Keep your answers concise, professional, and relevant to BiteHub's operations.
    """

    response = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
        temperature=0.4,
    )
    return response.choices[0].message["content"]


def run_ai_nonstaff(question):
    system_prompt = """
    You are BiteHub's Friendly Food Assistant.
    You help customers learn about menu items, food categories, prices, and deals.
    You should:
    - Be friendly, clear, and short.
    - Give recommendations from the menu.
    - Avoid technical or staff-only topics.
    """

    response = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
        temperature=0.6,
    )
    return response.choices[0].message["content"]

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
    # 🌆 Set login background
    set_background("back.jpg")

    st.markdown(
        """
        <h1 style='text-align: center; color: #FF6F61; font-size: 60px; margin-top: 20px;'>☕ BiteHub</h1>
        <p style='text-align: center; color: #dddddd; font-size: 18px;'>Welcome! Please log in below.</p>
        """,
        unsafe_allow_html=True
    )

    username = st.text_input("Username", placeholder="Enter username", key="login_username")
    password = st.text_input("Password", type="password", placeholder="Enter password", key="login_password")

    col1, col2, col3, col4, col5 = st.columns([1, 2, 2, 2, 1])
    with col2:
        if st.button("🔑 Log In", use_container_width=True):
            # 1️⃣ Hardcoded staff
            if username == "staff1" and password == "staff123":
                st.session_state.user = {"username": "staff1", "role": "Staff", "loyalty_points": 0}
                st.session_state.page = "main"
                st.success(f"✅ Welcome Staff {username}!")
                st.rerun()
            else:
                # 2️⃣ Database users
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
    # 🌆 Set signup background
    set_background("back.jpg")

    st.markdown("<h1 style='text-align: center; color: #FF6F61;'>📝 BiteHub — Sign Up</h1>", unsafe_allow_html=True)

    new_username = st.text_input("Create Username", key="new_user")
    new_password = st.text_input("Create Password", type="password", key="new_pass")
    confirm_password = st.text_input("Confirm Password", type="password", key="conf_pass")

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

    # ✅ Define role flags
    is_staff = (role == "Staff")
    is_guest = (role == "Guest")
    is_nonstaff = not is_staff and not is_guest  # optional but useful for clarity

    # ---------------------------
    # WELCOME / INFO MESSAGES
    # ---------------------------
    if not is_staff:  # 👈 Staff won't see this part
        st.info("👋 Welcome to BiteHub! Get ready for some amazing meals and updates! 😊")

        if is_guest:
            st.warning(
                "⚠️ You're currently browsing as a *Guest*.\n\n"
                "Create a free BiteHub account to enjoy full features:\n"
                "- Save your order history 🍽️\n"
                "- Submit feedback ⭐\n"
                "- Earn loyalty rewards 🎁\n"
                "- Get personalized offers 💌\n\n"
                "👉 Tap **Sign Up** from the login page to get started!"
            )

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

            # Load receipts data
            receipts = load_receipts_df()

            if receipts.empty:
                st.info("No sales data available yet.")
            else:
                # Normalize status
                receipts["status"] = receipts["status"].astype(str).str.strip().str.lower()
                receipts["timestamp"] = pd.to_datetime(receipts["timestamp"], errors="coerce")

                # --- Parse all items for metrics ---
                def parse_items(data):
                    if isinstance(data, str):
                        try:
                            parsed = json.loads(data)
                            if isinstance(parsed, list):
                                return parsed
                            elif isinstance(parsed, dict):
                                # old dict-style format
                                return [{"name": k, **v} for k, v in parsed.items()]
                        except Exception:
                            return []
                    elif isinstance(data, list):
                        return data
                    return []

                all_items = []
                for _, row in receipts.iterrows():
                    items = parse_items(row["items"])
                    for it in items:
                        if isinstance(it, dict):
                            qty = int(it.get("qty", 1))
                            name = it.get("name", str(it))
                        else:
                            name = str(it)
                            qty = 1
                        all_items.append({"Item": name, "Quantity Sold": qty})

                total_item_count = sum(i["Quantity Sold"] for i in all_items)
                total_sales = receipts["total"].astype(float).sum()
                ready_orders = (receipts["status"] == "ready").sum()
                completed_orders = (receipts["status"] == "completed").sum()

                # --- METRICS SECTION ---
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("🧾 Total Items Ordered", total_item_count)
                col2.metric("💰 Total Sales", f"₱{total_sales:,.2f}")
                col3.metric("✅ Completed Orders", completed_orders)
                col4.metric("🕒 Ready Orders", ready_orders)

                st.divider()

                # --- SALES BY DAY OF MONTH ---
                st.subheader("📅 Sales by Day of the Month")
                receipts["day_of_month"] = receipts["timestamp"].dt.day
                daily_sales = receipts.groupby("day_of_month")["total"].sum().reset_index()
                daily_sales.columns = ["Day of Month", "Total Sales"]

                fig, ax = plt.subplots(figsize=(6, 3))
                ax.plot(daily_sales["Day of Month"], daily_sales["Total Sales"], marker="o", linestyle='-', color='tab:blue')
                ax.set_title("Sales Activity by Day of the Month", fontsize=10)
                ax.set_xlabel("Day of Month", fontsize=8)
                ax.set_ylabel("Total Sales (₱)", fontsize=8)
                ax.tick_params(axis='x', rotation=45, labelsize=7)
                ax.tick_params(axis='y', labelsize=7)
                st.pyplot(fig)

                st.divider()

                # --- SALES BY DAY OF WEEK ---
                st.subheader("📆 Sales by Day of the Week")
                receipts["day_of_week"] = receipts["timestamp"].dt.dayofweek
                weekly_sales = receipts.groupby("day_of_week")["total"].sum().reset_index()
                weekly_sales.columns = ["Day of Week", "Total Sales"]
                weekly_sales["Day of Week"] = weekly_sales["Day of Week"].map({
                    0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday", 4: "Friday", 5: "Saturday", 6: "Sunday"
                })

                fig, ax = plt.subplots(figsize=(6, 3))
                ax.plot(weekly_sales["Day of Week"], weekly_sales["Total Sales"], marker="o", linestyle='-', color='tab:green')
                ax.set_title("Sales Activity by Day of the Week", fontsize=10)
                ax.set_xlabel("Day of Week", fontsize=8)
                ax.set_ylabel("Total Sales (₱)", fontsize=8)
                ax.tick_params(axis='x', rotation=45, labelsize=7)
                ax.tick_params(axis='y', labelsize=7)
                st.pyplot(fig)

                st.divider()

                # --- TOP SELLING ITEMS ---
                st.subheader("🏆 Top 5 Best-Selling Items")

                if all_items:
                    df_items = (
                        pd.DataFrame(all_items)
                        .groupby("Item", as_index=False)
                        .sum()
                        .sort_values(by="Quantity Sold", ascending=False)
                        .head(5)
                    )
                    st.bar_chart(df_items.set_index("Item"))
                else:
                    st.info("No item data yet.")

                st.divider()

                # --- RECENT ORDERS TABLE ---
                st.subheader("🕒 Recent Orders")
                st.dataframe(
                    receipts[["order_id", "user_id", "total", "status", "timestamp"]]
                    .head(10)
                    .rename(columns={
                        "order_id": "Order ID",
                        "user_id": "Customer",
                        "total": "Total (₱)",
                        "status": "Status",
                        "timestamp": "Time"
                    })
                )
                
        elif choice == "Pending Orders":
            st.markdown("<h2 style='color:#FF6F61;'>📦 Pending Orders</h2>", unsafe_allow_html=True)

            # 🔄 Manual refresh button
            if st.button("🔄 Refresh Orders"):
                st.session_state["last_refresh"] = datetime.now()
                st.rerun()

            # --- Load orders from Snowflake ---
            receipts = load_receipts_df()

            # --- Load local guest receipts ---
            local = st.session_state.get("_local_receipts", [])
            if local:
                local_df = pd.DataFrame(local)
                if "pickup_dt" in local_df.columns:
                    local_df = local_df.rename(columns={"pickup_dt": "pickup_time"})

                required_cols = ["order_id", "items", "total", "payment_method", "user_id", "pickup_time", "status", "timestamp"]
                for col in required_cols:
                    if col not in local_df.columns:
                        local_df[col] = None

                if receipts is None or receipts.empty:
                    receipts = local_df
                else:
                    receipts = pd.concat([receipts, local_df], ignore_index=True)

            if receipts is not None and not receipts.empty:
                receipts["user_id"] = receipts["user_id"].fillna("Guest").astype(str).str.strip()
                pending_orders = receipts[receipts["status"].astype(str).str.lower() == "pending"]
            else:
                pending_orders = pd.DataFrame()

            if not pending_orders.empty:
                for idx, row in pending_orders.iterrows():
                    order_id = row.get("order_id")
                    user_id = row.get("user_id") or "Guest"
                    payment = row.get("payment_method", "N/A")
                    total = row.get("total", 0)
                    pickup_time = row.get("pickup_time", "N/A")
                    items_html = ""

                    # 🧾 Build ordered items HTML
                    items = row.get("items")
                    if isinstance(items, str):
                        try:
                            items = json.loads(items)
                        except Exception:
                            pass

                    if isinstance(items, list):
                        for i in items:
                            items_html += f"<p style='color:black; font-size:14px; margin:3px 0;'>- {i.get('name','')} — Qty: {i.get('qty',1)} @ ₱{i.get('price',0)}</p>"
                    elif isinstance(items, dict):
                        for name, details in items.items():
                            items_html += f"<p style='color:black; font-size:14px; margin:3px 0;'>- {name} — Qty: {details.get('qty',1)} @ ₱{details.get('price',0)}</p>"

                    # 🧱 Render everything together in one HTML container
                    order_html = f"""
                        <div style="
                            background-color: #fff;
                            border: 2px solid #FF6F61;
                            border-radius: 15px;
                            padding: 15px 20px;
                            margin-bottom: 15px;
                            box-shadow: 2px 2px 8px rgba(0,0,0,0.1);
                            color: #000000;
                            display: flex;
                            justify-content: space-between;
                            align-items: flex-start;
                            flex-wrap: wrap;
                        ">
                            <div style="flex: 1; min-width: 250px;">
                                <h4 style='color:#FF6F61;'>Order #{order_id}</h4>
                                <p><b>User:</b> {user_id}</p>
                                <p><b>Payment:</b> {payment}</p>
                                <p><b>Total:</b> ₱{total:.2f}</p>
                                <p><b>Pickup Time:</b> {pickup_time}</p>
                            </div>
                            <div style="flex: 1; min-width: 250px;">
                                <h4 style='color:#FF6F61;'>🧾 Ordered Items:</h4>
                                {items_html}
                            </div>
                        </div>
                    """
                    st.markdown(order_html, unsafe_allow_html=True)

                    # ✅ Mark as Ready button with updated notification
                    if st.button("✅ Mark as Ready", key=f"ready_{order_id}"):
                        update_order_status(order_id, "Ready")
                        notify_user_id = row.get("user_id") or "Guest"
                        add_notification(
                            notify_user_id,
                            f"Your delicious order from BiteHub is hot and ready! "
                            f"Come by at your convenience and show #{order_id} to pick up your meal. Bon appétit!"
                        )
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

                    # Detect newly added items
                    old_items = set(menu_df["ITEM"].tolist())
                    new_items = set(edited["ITEM"].tolist()) - old_items

                    # Save updates
                    if st.button("💾 Save Menu Updates"):
                        upsert_menu(edited.drop(columns=["Delete"]))
                        st.success("✅ Menu updated successfully!")

                        # Notify all non-staff users about new items
                        for new_item in new_items:
                            all_users = get_all_non_staff_users()  # implement to fetch usernames of non-staff & guests
                            for u in all_users:
                                add_notification(
                                    u,
                                    f"Exciting news from BiteHub! We’ve just added a mouthwatering new item to our menu: {new_item}. Come in and try it today!"
                                )
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
            st.subheader("🤖 AI Assistant (Staff)")
            q = st.text_area("Ask AI something:", key="staff_ai_q")
            if st.button("Ask AI", key="ask_ai_staff"):
                st.write(run_ai(q, role="Staff"))

        elif choice == "Feedback Review":
            st.subheader("📢 Feedback Review")
            fb = load_feedbacks_df()

            if not fb.empty:
                st.dataframe(fb, use_container_width=True)
            else:
                st.info("No feedbacks yet.")

        elif choice == "Sales Report":
            st.subheader("💰 Sales Breakdown")

            # 🔄 Always load latest receipts from database
            receipts = load_receipts_df()
            local = st.session_state.get("_local_receipts", [])
            if local:
                receipts = pd.concat([receipts, pd.DataFrame(local)], ignore_index=True)

            if receipts.empty:
                st.info("No sales yet.")
            else:
                # 🩶 Include both 'Ready' and 'Completed' orders in the summary
                receipts["status"] = receipts["status"].astype(str).str.strip().str.lower()
                receipts = receipts[receipts["status"].isin(["ready", "completed"])]

                if receipts.empty:
                    st.info("No ready or completed orders yet.")
                else:
                    all_items = []

                    # ✅ IMPROVED PARSER — handles both old (list) and new (dict) formats
                    def parse_items(data):
                        """Handle items as list of dicts OR dict of dicts."""
                        if isinstance(data, str):
                            # Try to parse JSON safely
                            try:
                                data = json.loads(data)
                            except Exception:
                                # Fix single quotes or malformed JSON
                                try:
                                    data = json.loads(data.replace("'", '"'))
                                except Exception:
                                    return []

                        # Case 1: list of dicts (old format)
                        if isinstance(data, list):
                            return data

                        # Case 2: dict of dicts (new format)
                        if isinstance(data, dict):
                            items_list = []
                            for name, info in data.items():
                                if isinstance(info, dict):
                                    qty = int(info.get("qty", 1))
                                    price = float(info.get("price", 0))
                                else:
                                    qty = 1
                                    price = 0
                                items_list.append({
                                    "name": name.strip(),
                                    "qty": qty,
                                    "price": price
                                })
                            return items_list

                        return []

                    # 🔍 Loop through receipts and extract items
                    for _, row in receipts.iterrows():
                        items = parse_items(row.get("items", []))
                        for it in items:
                            name = it.get("name", "Unknown").strip()
                            qty = int(it.get("qty", 1))
                            all_items.append({"Item": name, "Quantity Sold": qty})

                    # 🧾 If still empty
                    if not all_items:
                        st.warning("No item sales data found yet.")
                    else:
                        sales_summary = (
                            pd.DataFrame(all_items)
                            .groupby("Item", as_index=False)
                            .sum()
                            .sort_values(by="Quantity Sold", ascending=False)
                        )

                        # --- PIE CHART WITH LEGEND BESIDE ---
                        fig, ax = plt.subplots(figsize=(6, 5))
                        wedges, texts, autotexts = ax.pie(
                            sales_summary["Quantity Sold"],
                            autopct="%1.1f%%",
                            startangle=90,
                            pctdistance=0.8,
                            labeldistance=1.1
                        )

                        ax.legend(
                            wedges,
                            sales_summary["Item"],
                            title="Items",
                            loc="center left",
                            bbox_to_anchor=(1, 0, 0.5, 1)
                        )

                        for autotext in autotexts:
                            autotext.set_color("black")
                            autotext.set_fontsize(8)

                        ax.axis("equal")
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
            
        elif choice == "AI Assistant":
            st.markdown("### 🤖 BiteHub Staff Assistant")
            with st.expander("💬 Ask BiteHub AI", expanded=False):
                q = st.text_input("Ask something:", key="staff_ai_q", placeholder="e.g. Suggest menu improvements or pricing ideas.")
                if st.button("Ask AI", key="ask_ai_staff"):
                    menu_df = load_menu()
                    menu_list = "\n".join([
                        f"{row['CATEGORY']} - {row['ITEM']} (₱{row['PRICE']})"
                        for _, row in menu_df.iterrows()
                    ])
                    prompt = f"""
                    You are BiteHub's staff AI assistant. 
                    You help canteen staff manage menu, pricing, ingredients, and operations.
                    Be concise, helpful, and only refer to menu items below.
                    Do NOT invent prices or items.

                    MENU:
                    {menu_list}

                    STAFF QUESTION: {q}
                    """
                    st.write(run_ai(prompt))

            # ---------------------------
            # MENU & ORDERING
            # ---------------------------
            st.markdown("### 📖 Menu & Ordering")

            if menu_df is None or menu_df.empty:
                st.warning("⚠️ Menu is empty.")
            else:
                # Loop through categories
                for cat in menu_df["CATEGORY"].dropna().unique():
                    with st.expander(f"🍽️ {cat}", expanded=False):
                        category_items = menu_df[menu_df["CATEGORY"] == cat]

                        # Display in 3-column grid
                        cols = st.columns(3)
                        col_index = 0

                        for idx, row in category_items.iterrows():
                            name = row["ITEM"]
                            price = float(row["PRICE"])
                            img = row["VALID_IMAGE"]  # <-- use the validated image URL

                            # Make a unique key using index + name
                            button_key = f"add_{idx}_{name}"

                            with cols[col_index]:
                                st.image(img, use_container_width=True)
                                st.markdown(f"**{name}**")
                                st.write(f"₱{price:.2f}")

                                if st.button("➕ Add to Cart", key=button_key):
                                    if "cart" not in st.session_state:
                                        st.session_state.cart = {}
                                    if name not in st.session_state.cart:
                                        st.session_state.cart[name] = {"qty": 1, "price": price}
                                    else:
                                        st.session_state.cart[name]["qty"] += 1
                                    st.success(f"{name} added to cart!")

                            col_index += 1
                            if col_index >= 3:
                                cols = st.columns(3)
                                col_index = 0

            # ---------------------------
            # CART SECTION
            # ---------------------------
            st.markdown("### 🛒 Your Cart")
            cart = st.session_state.get("cart", {})
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
                    from datetime import datetime
                    import uuid

                    user_id = st.session_state.get("user", {}).get("username", "Guest")
                    order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
                    payment_method = "Cash"
                    pickup_time = datetime.now()
                    status = "Pending"

                    save_receipt(
                        order_id,
                        cart,
                        total,
                        payment_method,
                        user_id,
                        pickup_time,
                        status
                    )

                    add_notification(
                        user_id,
                        f"🧾 Your order has been placed successfully! We'll notify you once it's ready for pickup."
                    )

                    st.success("✅ Order placed successfully!")
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

            # Detect if user is guest (no username or explicitly Guest)
            username = user.get("username", "")
            is_guest = not username or username.lower() == "guest"

            if is_guest:
                st.warning("Guests cannot submit feedbacks. Please log in to share your thoughts.")
            else:
                with st.form("feedback_form"):
                    item = st.selectbox("Item", menu_df["ITEM"].unique())
                    fb = st.text_area("Your feedback")
                    rt = st.slider("Rating", 1, 5, 3)
                    if st.form_submit_button("Submit"):
                        save_feedback(item, fb, rt, username)
                        st.success("✅ Feedback submitted!")

            # ---------------------------
            # 📢 Notifications Section
            # ---------------------------
            st.divider()
            st.subheader("📢 Notifications")

            # Use "Guest" if user is not logged in
            user_id = user.get("username") if user.get("username") else "Guest"

            # 🔄 Manual Refresh
            if st.button("🔄 Refresh Data"):
                st.session_state["manual_refresh"] = True
                st.rerun()

            if st.session_state.get("manual_refresh"):
                # You can reload data here if needed
                st.session_state["manual_refresh"] = False

            # Load notifications for this user
            notes = get_notifications_for_user(user_id)
            if notes:
                for n in notes:
                    st.info(n)
            else:
                st.info("No notifications yet.")

            # 🧹 Clear Notifications
            if st.button("🧹 Clear Notifications"):
                clear_notifications_for_user(user_id)
                st.success("✅ All notifications cleared.")
                st.rerun()

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

            # Add notification for feedback request
            feedback_message = (
                f"We value your opinion! How was your recent dining experience at BiteHub? "
                f"Please take a moment to provide us with your feedback. Your input helps us improve."
            )
            add_notification(user_id, feedback_message)

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
