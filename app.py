from flask import Flask, request, render_template, url_for, redirect, flash, session, send_from_directory, jsonify
import os, uuid, re, time
from werkzeug.utils import secure_filename
import mysql.connector
from rapidfuzz import process, fuzz
import json
from mysql.connector import Error as MySQLError
from urllib.parse import urlparse
from flask import request
from pyproj import Transformer
import re
import subprocess

# EGSA '87 (EPSG:2100) -> WGS84 (EPSG:4326)
egsa2100_to_wgs84 = Transformer.from_crs(2100, 4326, always_xy=True)

def parse_egsa_pair(cell: str):
    if cell is None: return (None, None)
    s = str(cell).strip()
    if not s or s == '-': return (None, None)
    s = s.split('/', 1)[0]                      # drop chainage after '/'
    s = s.replace('，', ',').replace(';', ',').replace(',', ' ')
    nums = re.findall(r'[-+]?\d+(?:\.\d+)?', s)
    if len(nums) >= 2:
        x, y = float(nums[0]), float(nums[1])
        if 100_000 <= x <= 900_000 and 4_000_000 <= y <= 4_500_000:
            return x, y
    return (None, None)

# ---------- filename helpers ----------
# allow Greek & Latin letters/numbers; replace others with dashes; collapse dashes
GREEK_LATIN_ALNUM = re.compile(r"[^0-9A-Za-z\u0370-\u03FF\u1F00-\u1FFF]+", re.UNICODE)
MULTI_DASH = re.compile(r"-{2,}")

def slugify_text(txt: str) -> str:
    s = os.path.basename(txt)
    s = secure_filename(s)                    # baseline sanitization
    s = GREEK_LATIN_ALNUM.sub("-", s)         # keep Greek + Latin
    s = MULTI_DASH.sub("-", s).strip("-").lower()
    return s or "file"

def slugify_category(cat: str) -> str:
    return slugify_text(cat)

def ensure_unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while True:
        cand = f"{base}-{i:03d}{ext}"
        if not os.path.exists(cand):
            return cand
        i += 1


# ----------------------------
# Flask app & auth
# ----------------------------
app = Flask(__name__)
app.secret_key = "61d6723ed55307361fee47492eb2aa39cb465c0429c04fd1b1b2c0db0ba4d679"
ADMIN_CODE = "8800"  # change this!

# ----------------------------
# Uploads config (store files on disk, keep filenames in DB)
# ----------------------------
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_IMAGES = {"png", "jpg", "jpeg", "webp", "gif"}
ALLOWED_DOCS = {"pdf"}  # new
DOC_CATEGORIES = (
    "Επιθεώρηση",
    "Σχέδιο",
    "Αποτελέσματα παρακολούθησης δομικής ακεραιότητας κατασκευής",
    "Επισκευές και Παρεμβάσεις",
)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 10MB per request

def allowed_file(filename: str, allowed_exts) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed_exts



def save_image(file_storage, *, bridge_id: int, category: str):
    """Save image under uploads/bridge_<id>/<category-slug>/..., return relative path (under uploads/)."""
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_file(file_storage.filename, ALLOWED_IMAGES):
        return None

    ts = time.strftime("%Y-%m-%d_%H%M%S")
    cat_slug = slugify_category(category)
    root = app.config["UPLOAD_FOLDER"]
    subdir = os.path.join(root, f"bridge_{bridge_id}", cat_slug)
    os.makedirs(subdir, exist_ok=True)

    original = slugify_text(file_storage.filename)
    fname = f"b{bridge_id:05d}_{cat_slug}__{ts}__{original}"
    abs_path = ensure_unique_path(os.path.join(subdir, fname))
    file_storage.save(abs_path)

    return os.path.relpath(abs_path, root)


def save_doc(file_storage, *, bridge_id: int, category: str):
    """Save PDF under uploads/bridge_<id>/<category-slug>/..., return relative path (under uploads/)."""
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_file(file_storage.filename, ALLOWED_DOCS):
        return None

    ts = time.strftime("%Y-%m-%d_%H%M%S")
    cat_slug = slugify_category(category)
    root = app.config["UPLOAD_FOLDER"]
    subdir = os.path.join(root, f"bridge_{bridge_id}", cat_slug)
    os.makedirs(subdir, exist_ok=True)

    original = slugify_text(file_storage.filename)
    fname = f"b{bridge_id:05d}_{cat_slug}__{ts}__{original}"
    abs_path = ensure_unique_path(os.path.join(subdir, fname))
    file_storage.save(abs_path)

    return os.path.relpath(abs_path, root)
@app.route("/run-backup", methods=["POST"])
def run_backup():
    if session.get("role") != "admin":
        flash("Πρέπει να είσαι admin.")
        return redirect(url_for("index"))

    try:
        script_path = os.path.join(os.path.dirname(__file__), "backup.sh")
        result = subprocess.run(
            ["bash", script_path],
            capture_output=True,
            text=True,
            check=True
        )
        flash("Το backup εκτελέστηκε επιτυχώς.")
        print("Backup output:", result.stdout)
    except subprocess.CalledProcessError as e:
        flash("Σφάλμα κατά την εκτέλεση του backup.")
        print("Backup error:", e.stderr)
    return redirect(url_for("index"))
@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

# ----------------------------
# MySQL connection
# ----------------------------
db = mysql.connector.connect(
    host="localhost",
    user="bridges",
    password="8800",
    database="database",
    charset="utf8mb4",
    use_unicode=True
)
cursor = db.cursor(dictionary=True)

# ----------------------------
# Auth: login/logout
# ----------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        code = request.form.get("code", "")
        if code == ADMIN_CODE:
            session["role"] = "admin"
            flash("Logged in as admin.")
        else:
            session["role"] = "guest"
            flash("Logged in as guest.")
        return redirect(url_for("index"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("role", None)
    flash("Logged out.")
    return redirect(url_for("index"))

# ----------------------------
# Home (index) + Search List redirect + Map
# ----------------------------
@app.route("/")
def startup():
    return render_template("start.html")

@app.route("/main")
def index():
    # Απλή αρχική σελίδα με 3 επιλογές
    return render_template("index.html")

@app.route("/map")
def map_view():
    bridges = []
    try:
        cursor.execute("""
            SELECT id, `Όνομα` AS name, `Νομός` AS nomos, `Συντεταγμένες ΕΓΣΑ` AS egsa
            FROM bridges
            WHERE `Συντεταγμένες ΕΓΣΑ` IS NOT NULL
              AND TRIM(`Συντεταγμένες ΕΓΣΑ`) <> ''
            ORDER BY id DESC
        """)
        for r in cursor.fetchall():
            x, y = parse_egsa_pair(r["egsa"])
            if x is None: continue
            lon, lat = egsa2100_to_wgs84.transform(x, y)
            bridges.append({
                "id": r["id"],
                "name": r["name"],
                "nomos": r["nomos"],
                "lat": float(lat),
                "lon": float(lon),
            })
    except Exception as e:
        print("Map conversion error:", e)

    return render_template(
        "map.html",
        has_latlon=bool(bridges),
        bridges_json=json.dumps(bridges, ensure_ascii=False)
    )

@app.route("/search-list")
def search_list():
    cursor.execute("""
        SELECT id, `Όνομα`, `Άξονας`
        FROM bridges
        ORDER BY list_order ASC, id DESC
    """)
    results = cursor.fetchall()
    return render_template("list.html", results=results)

@app.route("/update-order", methods=["POST"])
def update_order():
    # Security: only admins can reorder
    if session.get("role") != "admin":
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json()
    new_order = data.get("order", [])

    try:
        # Loop through the list of IDs sent by the browser
        # and update their position (0, 1, 2, etc.)
        for index, bridge_id in enumerate(new_order):
            cursor.execute(
                "UPDATE bridges SET list_order = %s WHERE id = %s",
                (index, bridge_id)
            )
        db.commit()
        return jsonify({"success": True})
    except Exception as e:
        print("Order update error:", e)
        return jsonify({"error": str(e)}), 500
# ----------------------------
# Search (shows all on GET or empty query; fuzzy on query)
# ----------------------------
@app.route("/search", methods=["GET", "POST"])
def search():
    results = []
    if request.method == "POST":
        query = (request.form.get("query") or "").strip()

        if not query:
            # No search → show all bridges, newest first
            cursor.execute("""
                SELECT id, `Όνομα`, `Άξονας`, `Υπεύθυνος Λειτουργίας-Συντήρησης`, `Κατάσταση`
                FROM bridges
                ORDER BY id DESC
            """)
            results = cursor.fetchall()
        else:
            # Case-insensitive match in multiple fields
            like = f"%{query.lower()}%"
            cursor.execute("""
                SELECT id, `Όνομα`, `Άξονας`, `Υπεύθυνος Λειτουργίας-Συντήρησης`, `Κατάσταση`
                FROM bridges
                WHERE LOWER(`Όνομα`) LIKE %s
                   OR LOWER(`Άξονας`) LIKE %s
                   OR LOWER(`Υπεύθυνος Λειτουργίας-Συντήρησης`) LIKE %s
                   OR LOWER(`Κατάσταση`) LIKE %s
                ORDER BY id DESC
            """, (like, like, like, like))
            results = cursor.fetchall()
    else:
        # Initial page load → show all bridges
        cursor.execute("""
            SELECT id, `Όνομα`, `Άξονας`, `Υπεύθυνος Λειτουργίας-Συντήρησης`, `Κατάσταση`
            FROM bridges
            ORDER BY id DESC
        """)
        results = cursor.fetchall()

    return render_template("search.html", results=results)


# ----------------------------
# Bridge details (includes images + docs)
# ----------------------------
@app.route("/bridge/<int:bridge_id>")
def bridge_details(bridge_id):
    cursor.execute("SELECT * FROM bridges WHERE id = %s", (bridge_id,))
    bridge = cursor.fetchone()
    if not bridge:
        return "Δεν βρέθηκε η γέφυρα", 404
        
    # Υπολογισμός συντεταγμένων σε WGS84
    lat, lon = None, None
    if bridge.get("Συντεταγμένες ΕΓΣΑ"):
        x, y = parse_egsa_pair(bridge["Συντεταγμένες ΕΓΣΑ"])
        if x and y:
            lon, lat = egsa2100_to_wgs84.transform(x, y)
            
    # Εικόνες
    cursor.execute("""
        SELECT id, category, filename
        FROM bridge_images
        WHERE bridge_id = %s
        ORDER BY id
    """, (bridge_id,))
    imgs = cursor.fetchall()
    images = {"Αρμοί": [], "Όψεις": []}
    for r in imgs:
        images.setdefault(r["category"], []).append(r)

    # PDFs: Επιθεωρήσεις
    cursor.execute("""
        SELECT id, filename, COALESCE(label, filename) AS label
        FROM bridge_docs
        WHERE bridge_id = %s AND category='Επιθεώρηση'
        ORDER BY created_at DESC, id DESC
    """, (bridge_id,))
    docs = cursor.fetchall()

    # PDFs: Σχέδια "ΩΣ ΚΑΤΑΣΚΕΥΑΣΘΗ"
    cursor.execute("""
        SELECT id, filename, COALESCE(label, filename) AS label
        FROM bridge_docs
        WHERE bridge_id = %s AND category='Σχέδιο'
        ORDER BY created_at DESC, id DESC
    """, (bridge_id,))
    plans = cursor.fetchall()

    # PDFs: Αποτελέσματα παρακολούθησης δομικής ακεραιότητας κατασκευής
    cursor.execute("""
        SELECT id, filename, COALESCE(label, filename) AS label
        FROM bridge_docs
        WHERE bridge_id = %s AND category='Αποτελέσματα παρακολούθησης δομικής ακεραιότητας κατασκευής'
        ORDER BY created_at DESC, id DESC
    """, (bridge_id,))
    monitoring_results = cursor.fetchall()

    # PDFs: Επισκευές και Παρεμβάσεις
    cursor.execute("""
        SELECT id, filename, COALESCE(label, filename) AS label
        FROM bridge_docs
        WHERE bridge_id = %s AND category='Επισκευές και Παρεμβάσεις'
        ORDER BY created_at DESC, id DESC
    """, (bridge_id,))
    repairs = cursor.fetchall()

    return render_template(
        "bridge_details.html",
        bridge=bridge,
        images=images,
        docs=docs,
        plans=plans,
        monitoring_results=monitoring_results,
        repairs=repairs,
        lat=lat,
        lon=lon,
    )

# ----------------------------
# Add bridge (admin only) + image uploads
# ----------------------------
@app.route("/add", methods=["GET", "POST"])
def add_bridge():
    if session.get("role") != "admin":
        flash("You must be admin to add bridges.")
        return redirect(url_for("search"))

    fields = [
        "Όνομα", "Κωδικός", "Νομός", "Άξονας", "Ημερομηνία Κατασκευής", "Συντεταγμένες ΕΓΣΑ",
        "Είδος Γέφυρας", "Είδος Γεφύρωσης", "Κατάσταση", "Κύριος του Έργου",
        "Υπεύθυνος Λειτουργίας-Συντήρησης", "Στατικό σύστημα φορέα ανωδομής",
        "Πλήθος ανοιγμάτων", "Μήκη ανοιγμάτων", "Τύπος φορέα ανωδομής",
        "Τύπος ακροβάθρων", "Τύπος μεσοβάθρων", "Υλικό ακροβάθρων",
        "Στόμια αποχέτευσης", "Στηθαία ασφαλείας", "Αρμοί", "Παρατηρήσεις"
    ]

    if request.method == "POST":
        form = request.form
        if not form.get("Όνομα"):
            flash("Το πεδίο Όνομα είναι υποχρεωτικό.")
            return render_template("add_bridge.html")

        sql = f"""
            INSERT INTO bridges ({', '.join(f'`{f}`' for f in fields)})
            VALUES ({', '.join(['%s'] * len(fields))})
        """
        values = tuple(form.get(field) or None for field in fields)
        cursor.execute(sql, values)
        db.commit()
        bridge_id = cursor.lastrowid

        for fs in request.files.getlist("armoi_images"):
            fn = save_image(fs, bridge_id=bridge_id, category="Αρμοί")
            if fn:
                cursor.execute(
                    "INSERT INTO bridge_images (bridge_id, category, filename) VALUES (%s,%s,%s)",
                    (bridge_id, "Αρμοί", fn)
                )

        opseis_files = [fs for fs in request.files.getlist("opseis_images") if fs and fs.filename][:2]
        for fs in opseis_files:
            fn = save_image(fs, bridge_id=bridge_id, category="Όψεις")
            if fn:
                cursor.execute(
                    "INSERT INTO bridge_images (bridge_id, category, filename) VALUES (%s,%s,%s)",
                    (bridge_id, "Όψεις", fn)
                )
        db.commit()
        flash("Η γέφυρα προστέθηκε επιτυχώς.")
        return redirect(url_for("search"))

    return render_template("add_bridge.html")

# ----------------------------
# Edit bridge (admin only)
# ----------------------------
@app.route("/bridge/<int:bridge_id>/edit", methods=["GET","POST"])
def edit_bridge(bridge_id):
    if session.get("role") != "admin":
        flash("Πρέπει να είσαι admin.")
        return redirect(url_for("bridge_details", bridge_id=bridge_id))

    cursor.execute("SELECT * FROM bridges WHERE id = %s", (bridge_id,))
    bridge = cursor.fetchone()
    if not bridge:
        return "Δεν βρέθηκε η γέφυρα", 404

    fields = [
        "Όνομα", "Κωδικός", "Νομός", "Άξονας", "Ημερομηνία Κατασκευής", "Συντεταγμένες ΕΓΣΑ",
        "Είδος Γέφυρας", "Είδος Γεφύρωσης", "Κατάσταση", "Κύριος του Έργου",
        "Υπεύθυνος Λειτουργίας-Συντήρησης", "Στατικό σύστημα φορέα ανωδομής",
        "Πλήθος ανοιγμάτων", "Μήκη ανοιγμάτων", "Τύπος φορέα ανωδομής",
        "Τύπος ακροβάθρων", "Τύπος μεσοβάθρων", "Υλικό ακροβάθρων",
        "Στόμια αποχέτευσης", "Στηθαία ασφαλείας", "Αρμοί", "Παρατηρήσεις"
    ]

    if request.method == "POST":
        data = [request.form.get(f) or None for f in fields]
        set_clause = ", ".join(f"`{f}`=%s" for f in fields)
        cursor.execute(f"UPDATE bridges SET {set_clause} WHERE id=%s", (*data, bridge_id))
        db.commit()
        flash("Τα στοιχεία ενημερώθηκαν.")
        return redirect(url_for("bridge_details", bridge_id=bridge_id))

    return render_template("edit_bridge.html", bridge=bridge, fields=fields)

@app.post("/bridge/<int:bridge_id>/update-remarks")
def update_remarks(bridge_id):
    if session.get("role") != "admin":
        flash("Πρέπει να είσαι admin.")
        return redirect(url_for("bridge_details", bridge_id=bridge_id))

    new_remarks = request.form.get("remarks")
    
    try:
        cursor.execute(
            "UPDATE bridges SET `Παρατηρήσεις` = %s WHERE id = %s",
            (new_remarks, bridge_id)
        )
        db.commit()
        flash("Οι παρατηρήσεις ενημερώθηκαν επιτυχώς.")
    except Exception as e:
        print("Error updating remarks:", e)
        flash("Σφάλμα κατά την ενημέρωση.")
        
    return redirect(url_for("bridge_details", bridge_id=bridge_id))
# ----------------------------
# Add/Delete images from details (admin only)
# ----------------------------
@app.post("/bridge/<int:bridge_id>/images/add")
def add_bridge_images(bridge_id):
    if session.get("role") != "admin":
        flash("Πρέπει να είσαι admin.")
        return redirect(url_for("bridge_details", bridge_id=bridge_id))

    category = request.form.get("category")

    # ✅ Allow images in these 4 categories now
    ALLOWED_IMAGE_CATEGORIES = ("Αρμοί", "Όψεις", "Σχέδιο", "Επισκευές και Παρεμβάσεις")
    if category not in ALLOWED_IMAGE_CATEGORIES:
        flash("Μη έγκυρη κατηγορία.")
        return redirect(url_for("bridge_details", bridge_id=bridge_id))

    files = [fs for fs in request.files.getlist("images") if fs and fs.filename]

    # Keep the 2-image limit ONLY for Όψεις (as before)
    #if category == "Όψεις":
    #    cursor.execute("SELECT COUNT(*) AS c FROM bridge_images WHERE bridge_id=%s AND category='Όψεις'", (bridge_id,))
    #    count = cursor.fetchone()["c"]
    #    if count >= 2:
    #        flash("Υπάρχουν ήδη 2 εικόνες για Όψεις.")
    #        return redirect(url_for("bridge_details", bridge_id=bridge_id))
    #    files = files[: max(0, 2 - count)]
    
    added = 0
    for fs in files:
        fn = save_image(fs, bridge_id=bridge_id, category=category)
        if fn:
            cursor.execute(
                "INSERT INTO bridge_images (bridge_id, category, filename) VALUES (%s,%s,%s)",
                (bridge_id, category, fn)
            )
            added += 1
    db.commit()
    flash(f"Προστέθηκαν {added} εικόνα(ες) στην κατηγορία {category}.")
    return redirect(url_for("bridge_details", bridge_id=bridge_id))

@app.post("/bridge-image/<int:image_id>/delete")
def delete_bridge_image(image_id):
    if session.get("role") != "admin":
        flash("Πρέπει να είσαι admin.")
        return redirect(url_for("search"))

    cursor.execute("SELECT filename, bridge_id FROM bridge_images WHERE id = %s", (image_id,))
    row = cursor.fetchone()
    if not row:
        flash("Η εικόνα δεν βρέθηκε.")
        return redirect(url_for("search"))

    cursor.execute("DELETE FROM bridge_images WHERE id = %s", (image_id,))
    db.commit()

    try:
        os.remove(os.path.join(app.config["UPLOAD_FOLDER"], row["filename"]))
    except FileNotFoundError:
        pass

    flash("Η εικόνα διαγράφηκε.")
    return redirect(url_for("bridge_details", bridge_id=row["bridge_id"]))

# ----------------------------
# Add/Delete PDFs (admin only)
# ----------------------------
@app.post("/bridge/<int:bridge_id>/<category>/add")
def add_bridge_files(bridge_id, category):
    if session.get("role") != "admin":
        flash("Πρέπει να είσαι admin.")
        return redirect(url_for("bridge_details", bridge_id=bridge_id))

    if category not in DOC_CATEGORIES:
        flash("Μη έγκυρη κατηγορία.")
        return redirect(url_for("bridge_details", bridge_id=bridge_id))

    files = [fs for fs in request.files.getlist("files") if fs and fs.filename]
    added = 0
    for fs in files:
        fn = save_doc(fs, bridge_id=bridge_id, category=category)
        if fn:
            cursor.execute(
                "INSERT INTO bridge_docs (bridge_id, filename, label, category) VALUES (%s,%s,%s,%s)",
                (bridge_id, fn, fs.filename[:255], category)
            )
            added += 1
    db.commit()
    flash(f"Προστέθηκαν {added} PDF στην κατηγορία {category}.")
    return redirect(url_for("bridge_details", bridge_id=bridge_id) + "#docs")



# Διαγραφή ενός Σχεδίου (admin)
@app.post("/bridge-file/<int:file_id>/delete")
def delete_bridge_file(file_id):
    if session.get("role") != "admin":
        flash("Πρέπει να είσαι admin.")
        return redirect(url_for("search"))
    cursor.execute("SELECT filename, bridge_id FROM bridge_docs WHERE id=%s", (file_id,))
    row = cursor.fetchone()
    if not row:
        flash("Το αρχείο δεν βρέθηκε.")
        return redirect(url_for("search"))
    cursor.execute("DELETE FROM bridge_docs WHERE id=%s", (file_id,))
    db.commit()
    try:
        os.remove(os.path.join(app.config["UPLOAD_FOLDER"], row["filename"]))
    except FileNotFoundError:
        pass
    flash("Το αρχείο διαγράφηκε.")
    return redirect(url_for("bridge_details", bridge_id=row["bridge_id"]))


# ----------------------------
# Delete bridge (admin only)
# ----------------------------
@app.post("/bridge/<int:bridge_id>/delete")
def delete_bridge(bridge_id):
    if session.get("role") != "admin":
        flash("Πρέπει να είσαι admin για διαγραφή.")
        return redirect(url_for("search"))

    cursor.execute("SELECT `Όνομα` FROM bridges WHERE id = %s", (bridge_id,))
    row = cursor.fetchone()
    if not row:
        flash("Η γέφυρα δεν βρέθηκε.")
        # try to go back where we came from
        next_url = request.form.get("next") or request.referrer
        if next_url and urlparse(next_url).netloc in ("", request.host):
            return redirect(next_url)
        return redirect(url_for("search"))

    # collect related file paths (images+docs) before deleting
    cursor.execute("SELECT filename FROM bridge_images WHERE bridge_id = %s", (bridge_id,))
    filenames = [r["filename"] for r in cursor.fetchall()]
    cursor.execute("SELECT filename FROM bridge_docs WHERE bridge_id = %s", (bridge_id,))
    filenames += [r["filename"] for r in cursor.fetchall()]

    cursor.execute("DELETE FROM bridges WHERE id = %s", (bridge_id,))
    db.commit()

    for fn in filenames:
        try:
            os.remove(os.path.join(app.config["UPLOAD_FOLDER"], fn))
        except FileNotFoundError:
            pass

    flash(f"Διαγράφηκε η γέφυρα: {row['Όνομα']}")

    # prefer explicit next from form; else safe referrer; else fallback
    next_url = request.form.get("next") or request.referrer
    if next_url and urlparse(next_url).netloc in ("", request.host):
        return redirect(next_url)
    return redirect(url_for("search"))

# ----------------------------
# Main
# ----------------------------
#if __name__ == "__main__":
#    app.run(debug=True)
    
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
