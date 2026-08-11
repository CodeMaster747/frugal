"""Bootstrap training data.

A classifier needs labels before a user has corrected anything, so the model
ships with a seed corpus of merchant strings mapped to the system taxonomy.

Two things make this honest rather than a shortcut:

* Entries are **normalised merchant strings**, the same form
  `normalize_merchant` produces at write time. Training on raw bank narrations
  would teach the model to recognise terminal ids.
* Labels are slugs from `taxonomy.py`, so the label space is exactly the one
  the product uses. A category added to the taxonomy without examples here is
  simply one the model cannot predict yet -- visible, not silent.

Skewed to India because that is the target market (SRS §5.3), with global
merchants where they are common there.
"""

from __future__ import annotations

# merchant substring -> category slug
SEED_CORPUS: dict[str, str] = {
    # --- groceries ---
    "reliance fresh": "groceries",
    "reliance smart": "groceries",
    "bigbasket": "groceries",
    "big basket": "groceries",
    "dmart": "groceries",
    "d mart": "groceries",
    "more supermarket": "groceries",
    "spencers": "groceries",
    "nature's basket": "groceries",
    "blinkit": "groceries",
    "zepto": "groceries",
    "instamart": "groceries",
    "star bazaar": "groceries",
    "grofers": "groceries",
    "licious": "groceries",
    # --- food delivery ---
    "swiggy": "food-delivery",
    "zomato": "food-delivery",
    "eatsure": "food-delivery",
    "faasos": "food-delivery",
    "box8": "food-delivery",
    "dominos": "food-delivery",
    "pizza hut": "food-delivery",
    "mcdonalds": "food-delivery",
    "kfc": "food-delivery",
    "burger king": "food-delivery",
    "subway": "food-delivery",
    # --- dining out ---
    "starbucks": "dining-out",
    "cafe coffee day": "dining-out",
    "ccd": "dining-out",
    "blue tokai": "dining-out",
    "third wave coffee": "dining-out",
    "barbeque nation": "dining-out",
    "haldirams": "dining-out",
    "saravana bhavan": "dining-out",
    "chaayos": "dining-out",
    "social": "dining-out",
    # --- fuel ---
    "indian oil": "fuel",
    "iocl": "fuel",
    "bharat petroleum": "fuel",
    "bpcl": "fuel",
    "hindustan petroleum": "fuel",
    "hpcl": "fuel",
    "shell": "fuel",
    "nayara": "fuel",
    "petrol pump": "fuel",
    # --- ride hailing / transport ---
    "uber": "ride-hailing",
    "ola": "ride-hailing",
    "rapido": "ride-hailing",
    "namma yatri": "ride-hailing",
    "metro card": "public-transport",
    "bmrcl": "public-transport",
    "dmrc": "public-transport",
    "irctc": "public-transport",
    "redbus": "public-transport",
    "bmtc": "public-transport",
    # --- shopping ---
    "amazon": "shopping",
    "flipkart": "shopping",
    "myntra": "shopping",
    "ajio": "shopping",
    "meesho": "shopping",
    "nykaa": "shopping",
    "decathlon": "shopping",
    "ikea": "shopping",
    "westside": "shopping",
    "lifestyle": "shopping",
    "pantaloons": "shopping",
    "zara": "shopping",
    "h and m": "shopping",
    "uniqlo": "shopping",
    # --- electronics ---
    "croma": "electronics",
    "reliance digital": "electronics",
    "vijay sales": "electronics",
    "apple store": "electronics",
    "samsung": "electronics",
    "boat lifestyle": "electronics",
    # --- healthcare ---
    "apollo pharmacy": "healthcare",
    "pharmeasy": "healthcare",
    "netmeds": "healthcare",
    "1mg": "healthcare",
    "practo": "healthcare",
    "fortis": "healthcare",
    "manipal hospital": "healthcare",
    "dr lal pathlabs": "healthcare",
    "thyrocare": "healthcare",
    # --- insurance ---
    "lic": "insurance",
    "hdfc life": "insurance",
    "icici lombard": "insurance",
    "star health": "insurance",
    "policybazaar": "insurance",
    # --- entertainment ---
    "pvr": "entertainment",
    "inox": "entertainment",
    "bookmyshow": "entertainment",
    "cinepolis": "entertainment",
    "wonderla": "entertainment",
    # --- subscriptions ---
    "netflix": "subscriptions",
    "spotify": "subscriptions",
    "amazon prime": "subscriptions",
    "hotstar": "subscriptions",
    "disney plus": "subscriptions",
    "youtube premium": "subscriptions",
    "apple icloud": "subscriptions",
    "google one": "subscriptions",
    "cult fit": "subscriptions",
    "cultfit": "subscriptions",
    "audible": "subscriptions",
    "adobe": "subscriptions",
    "notion": "subscriptions",
    "chatgpt": "subscriptions",
    # --- utilities ---
    "bescom": "utilities",
    "tata power": "utilities",
    "adani electricity": "utilities",
    "mseb": "utilities",
    "indane gas": "utilities",
    "bharat gas": "utilities",
    "water board": "utilities",
    # --- internet / mobile ---
    "airtel broadband": "internet",
    "act fibernet": "internet",
    "jiofiber": "internet",
    "hathway": "internet",
    "excitel": "internet",
    "airtel prepaid": "mobile",
    "jio recharge": "mobile",
    "vodafone idea": "mobile",
    "vi recharge": "mobile",
    # --- rent ---
    "rent payment": "rent",
    "landlord": "rent",
    "nobroker rent": "rent",
    "housing society": "rent",
    "maintenance charges": "rent",
    # --- loan / emi ---
    "loan emi": "loan-emi",
    "auto loan": "loan-emi",
    "home loan": "loan-emi",
    "personal loan": "loan-emi",
    "credit card payment": "loan-emi",
    "bajaj finserv": "loan-emi",
    # --- education ---
    "byjus": "education",
    "unacademy": "education",
    "coursera": "education",
    "udemy": "education",
    "school fees": "education",
    "college fees": "education",
    # --- travel ---
    "makemytrip": "travel",
    "goibibo": "travel",
    "cleartrip": "travel",
    "indigo": "travel",
    "air india": "travel",
    "vistara": "travel",
    "oyo": "travel",
    "airbnb": "travel",
    "booking com": "travel",
    # --- salary / income ---
    "salary": "salary",
    "payroll": "salary",
    "neft salary": "salary",
    # --- investment ---
    "zerodha": "savings-investment",
    "groww": "savings-investment",
    "upstox": "savings-investment",
    "mutual fund": "savings-investment",
    "sip": "savings-investment",
    "ppf": "savings-investment",
    # --- fees ---
    "atm charges": "fees-charges",
    "annual fee": "fees-charges",
    "late payment fee": "fees-charges",
    "convenience fee": "fees-charges",
    "gst on": "fees-charges",
    # --- gifts ---
    "ferns n petals": "gifts-donations",
    "donation": "gifts-donations",
    "temple": "gifts-donations",
}


def training_pairs() -> list[tuple[str, str]]:
    """(merchant, slug) pairs for the initial fit."""
    return sorted(SEED_CORPUS.items())


def label_space() -> set[str]:
    return set(SEED_CORPUS.values())
