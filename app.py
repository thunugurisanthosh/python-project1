from flask import Flask, render_template, request, redirect, url_for, send_file, flash
import pandas as pd
import sqlite3
from datetime import datetime
import plotly.express as px
import io
import os

app = Flask(__name__)
app.secret_key = "change-me"  # for flashes
DB = "finance.db"

def ensure_db():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            category TEXT NOT NULL,
            amount REAL NOT NULL,
            type TEXT CHECK(type IN ('income','expense')) NOT NULL,
            notes TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_df():
    ensure_db()
    conn = sqlite3.connect(DB)
    df = pd.read_sql("SELECT * FROM transactions", conn)
    conn.close()
    if not df.empty:
        df["amount"] = df["amount"].astype(float)
        df["date"] = pd.to_datetime(df["date"])
    return df

@app.route("/")
def home():
    df = get_df()
    if df.empty:
        return render_template("dashboard.html",
                               income=0, expense=0, balance=0,
                               category_plot=None, trend_plot=None)

    total_income = df[df["type"] == "income"]["amount"].sum()
    total_expense = df[df["type"] == "expense"]["amount"].sum()
    balance = total_income - total_expense

    # Expense breakdown (pie)
    expense_df = df[df["type"] == "expense"]
    category_plot = None
    if not expense_df.empty:
        fig1 = px.pie(expense_df, values="amount", names="category",
                      title="Expense Breakdown by Category")
        category_plot = fig1.to_html(full_html=False)

    # Monthly trend (line)
    monthly = (df
               .assign(month=df["date"].dt.to_period("M").astype(str))
               .groupby(["month", "type"], as_index=False)["amount"].sum())
    fig2 = px.line(monthly, x="month", y="amount", color="type",
                   title="Monthly Income vs Expense")
    trend_plot = fig2.to_html(full_html=False)

    return render_template("dashboard.html",
                           income=round(total_income, 2),
                           expense=round(total_expense, 2),
                           balance=round(balance, 2),
                           category_plot=category_plot,
                           trend_plot=trend_plot)

@app.route("/add", methods=["POST"])
def add_transaction():
    date = request.form.get("date")
    category = request.form.get("category", "").strip()
    amount = request.form.get("amount")
    tx_type = request.form.get("type")
    notes = request.form.get("notes", "").strip()

    try:
        # Basic validation
        datetime.strptime(date, "%Y-%m-%d")
        amount = float(amount)
        if tx_type not in ("income", "expense"):
            raise ValueError("Invalid type")

        conn = sqlite3.connect(DB)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO transactions (date, category, amount, type, notes)
            VALUES (?, ?, ?, ?, ?)
        """, (date, category, amount, tx_type, notes))
        conn.commit()
        conn.close()
        flash("Transaction added ✅")
    except Exception as e:
        flash(f"Error: {e}")
    return redirect(url_for("home"))

# Simple vendor→category rules (optional auto-categorization aid)
VENDOR_RULES = {
    "uber": "Travel", "ola": "Travel",
    "zomato": "Food", "swiggy": "Food",
    "amazon": "Shopping", "flipkart": "Shopping",
    "electric": "Bills", "water": "Bills", "gas": "Bills",
    "salary": "Income"
}

def suggest_category(text):
    t = str(text).lower()
    for key, cat in VENDOR_RULES.items():
        if key in t:
            return cat
    return "Other"

def normalize_upload(df):
    # Accept flexible column names and standardize them
    cols = {c.lower().strip(): c for c in df.columns}
    rename_map = {}
    for want, options in {
        "date": ["date", "txn_date", "transaction_date"],
        "category": ["category", "merchant", "vendor", "description"],
        "amount": ["amount", "value", "amt"],
        "type": ["type", "txn_type", "credit_debit", "in_out"],
        "notes": ["notes", "remark", "memo", "narration"]
    }.items():
        for opt in options:
            if opt in cols:
                rename_map[cols[opt]] = want
                break
    df = df.rename(columns=rename_map)

    # Derive missing fields if possible
    if "type" not in df:
        # Positive = income, negative = expense (common bank exports)
        if "amount" in df:
            df["type"] = df["amount"].apply(lambda x: "income" if float(x) > 0 else "expense")
        else:
            df["type"] = "expense"
    if "category" not in df:
        df["category"] = df.get("notes", df.get("description", "Other")).apply(suggest_category)
    if "notes" not in df:
        df["notes"] = ""

    # Keep only required columns in order
    for col in ["date", "category", "amount", "type", "notes"]:
        if col not in df:
            raise ValueError(f"Missing column after normalization: {col}")

    # Coerce types/formats
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df["amount"] = df["amount"].astype(float)
    df["type"] = df["type"].str.lower().map(lambda x: "income" if "in" in x or x=="income" else "expense")
    df["category"] = df["category"].fillna("Other")
    df["notes"] = df["notes"].fillna("")

    return df[["date", "category", "amount", "type", "notes"]]

@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        file = request.files.get("file")
        if not file or not file.filename:
            flash("Please choose a file.")
            return redirect(url_for("upload"))
        try:
            if file.filename.lower().endswith(".csv"):
                df = pd.read_csv(file)
            else:
                df = pd.read_excel(file)  # requires openpyxl
            df = normalize_upload(df)

            conn = sqlite3.connect(DB)
            df.to_sql("transactions", conn, if_exists="append", index=False)
            conn.close()
            flash(f"Uploaded {len(df)} transactions ✅")
            return redirect(url_for("home"))
        except Exception as e:
            flash(f"Upload failed: {e}")
            return redirect(url_for("upload"))
    return render_template("upload.html")

@app.route("/export")
def export():
    df = get_df()
    if df.empty:
        flash("No data to export.")
        return redirect(url_for("home"))
    buf = io.StringIO()
    # Turn dates back to ISO strings
    out = df.copy()
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    out.to_csv(buf, index=False)
    mem = io.BytesIO(buf.getvalue().encode("utf-8"))
    mem.seek(0)
    # Flask ≥2.0 uses download_name (attachment_filename is deprecated)
    return send_file(mem, mimetype="text/csv", as_attachment=True,
                     download_name="finance_report.csv")

if __name__ == "__main__":
    ensure_db()
    app.run(debug=True)
