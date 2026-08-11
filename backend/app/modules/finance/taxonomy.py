"""The system category taxonomy.

Two levels, `user_id IS NULL`, shared by every user. It is also the label space
the categoriser is trained against (M5), so slugs are stable identifiers: adding
a category is safe, renaming a slug is a migration.
"""

from __future__ import annotations

from typing import NamedTuple


class Node(NamedTuple):
    slug: str
    name: str
    kind: str
    icon: str
    children: tuple[tuple[str, str], ...] = ()


TAXONOMY: tuple[Node, ...] = (
    Node("salary", "Salary", "income", "briefcase"),
    Node(
        "other-income",
        "Other Income",
        "income",
        "trending-up",
        (("freelance", "Freelance"), ("interest", "Interest"), ("refunds", "Refunds")),
    ),
    Node("rent", "Rent", "expense", "home"),
    Node(
        "food",
        "Food",
        "expense",
        "utensils",
        (
            ("groceries", "Groceries"),
            ("food-delivery", "Food Delivery"),
            ("dining-out", "Dining Out"),
        ),
    ),
    Node(
        "transport",
        "Transport",
        "expense",
        "car",
        (
            ("fuel", "Fuel"),
            ("ride-hailing", "Ride Hailing"),
            ("public-transport", "Public Transport"),
        ),
    ),
    Node(
        "utilities", "Utilities", "expense", "zap", (("internet", "Internet"), ("mobile", "Mobile"))
    ),
    Node("shopping", "Shopping", "expense", "shopping-bag", (("electronics", "Electronics"),)),
    Node("healthcare", "Healthcare", "expense", "heart-pulse", (("insurance", "Insurance"),)),
    Node("entertainment", "Entertainment", "expense", "clapperboard"),
    Node("subscriptions", "Subscriptions", "expense", "repeat"),
    Node("education", "Education", "expense", "graduation-cap"),
    Node("loan-emi", "Loan & EMI", "expense", "landmark"),
    Node("savings-investment", "Savings & Investment", "expense", "piggy-bank"),
    Node("travel", "Travel", "expense", "plane"),
    Node("gifts-donations", "Gifts & Donations", "expense", "gift"),
    Node("fees-charges", "Fees & Charges", "expense", "receipt"),
    Node("other-expense", "Other", "expense", "circle-dashed"),
    Node("transfer", "Transfer", "transfer", "arrow-left-right"),
)
