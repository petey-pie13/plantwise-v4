"""
update_ai_labels.py
───────────────────
Helper script to update the `ai_label` column in the `diseases` table.

Kindwise returns scientific names in lowercase (e.g. "hemileia vastatrix").
This script sets ai_label = LOWER(scientific_name) for every row that
already has a scientific_name but no ai_label.

You can also customise the CUSTOM_LABELS dict below for any disease where
the Kindwise label differs from the scientific name.

Run:
    python update_ai_labels.py
"""

import os
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

# ── Custom overrides ──────────────────────────────────────────────────────────
# Add entries here if the Kindwise API label differs from the scientific name.
# Key = exact disease name in your diseases table  (case-insensitive match)
# Val = the label Kindwise returns for this disease
CUSTOM_LABELS = {
    # Maize / Corn
    "Maize Streak Virus":          "maize streak virus",
    "Northern Corn Leaf Blight":   "exserohilum turcicum",
    "Common Rust":                 "puccinia sorghi",
    "Gray Leaf Spot":              "cercospora zeae-maydis",

    # Coffee
    "Coffee Leaf Rust":            "hemileia vastatrix",
    "Coffee Berry Disease":        "colletotrichum kahawae",
    "Coffee Wilt Disease":         "gibberella xylarioides",

    # Tomato / Potato
    "Late Blight":                 "phytophthora infestans",
    "Early Blight":                "alternaria solani",
    "Bacterial Wilt":              "ralstonia solanacearum",
    "Septoria Leaf Spot":          "septoria lycopersici",
    "Tomato Yellow Leaf Curl":     "tomato yellow leaf curl virus",

    # Beans
    "Bean Rust":                   "uromyces appendiculatus",
    "Anthracnose":                 "colletotrichum lindemuthianum",
    "Angular Leaf Spot":           "pseudocercosporella angulata",
    "Bean Common Mosaic Virus":    "bean common mosaic virus",

    # Banana / Plantain
    "Panama Disease":              "fusarium oxysporum f. cubense",
    "Black Sigatoka":              "mycosphaerella fijiensis",
    "Yellow Sigatoka":             "pseudocercospora musae",
    "Banana Xanthomonas Wilt":     "xanthomonas vasicola pv. musacearum",
}
# ─────────────────────────────────────────────────────────────────────────────

def get_connection():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 3306)),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "crop_advisory"),
    )


def main():
    conn = get_connection()
    cur  = conn.cursor(dictionary=True)

    # Fetch all diseases
    cur.execute("SELECT disease_id, name, scientific_name, ai_label FROM diseases")
    diseases = cur.fetchall()

    updated, skipped = 0, 0
    for d in diseases:
        # Check if we have a custom mapping first, regardless of existing ai_label
        custom = CUSTOM_LABELS.get(d["name"])
        if custom:
            new_label = custom
        elif d["scientific_name"]:
            # Derive from scientific name (Kindwise usually uses lowercase sci name)
            new_label = d["scientific_name"].lower().strip()
        else:
            skipped += 1
            print(f"  [SKIP]    #{d['disease_id']:3d}  {d['name']!r:40s}  -> no scientific name & no custom label")

            continue

        if d["ai_label"] == new_label:
            skipped += 1
            continue

        cur.execute(
            "UPDATE diseases SET ai_label = %s WHERE disease_id = %s",
            (new_label, d["disease_id"])
        )
        updated += 1
        print(f"  [UPDATE]  #{d['disease_id']:3d}  {d['name']!r:40s}  -> {new_label!r}")


    conn.commit()
    print(f"\nDone: {updated} updated, {skipped} unchanged/skipped.")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
