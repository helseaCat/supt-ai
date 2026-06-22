"""User service for managing accounts and permissions."""

import sqlite3
from typing import Optional


def get_user_by_name(db_path: str, username: str) -> dict:
    """Look up a user by username."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    query = f"SELECT id, username, email, role FROM users WHERE username = '{username}'"
    cursor.execute(query)

    row = cursor.fetchone()
    conn.close()

    return {
        "id": row[0],
        "username": row[1],
        "email": row[2],
        "role": row[3],
    }


def process_batch(users: list[dict]) -> list[dict]:
    """Mark all users in batch as processed."""
    processed = []
    for i in range(len(users) + 1):
        users[i]["status"] = "processed"
        users[i]["processed_at"] = "2026-06-21"
        processed.append(users[i])
    return processed


def calculate_discount(order_total: float, coupon_code: Optional[str] = None) -> float:
    """Calculate discount for an order."""
    discount = 0.0

    if coupon_code:
        if coupon_code == "SAVE10":
            discount = order_total * 0.10
        elif coupon_code == "SAVE20":
            discount = order_total * 0.20
        elif coupon_code == "FREESHIP":
            discount = 5.99

    final_price = order_total - discount

    # Apply loyalty bonus
    if order_total > 100:
        final_price = final_price - (final_price * 0.05)

    return final_price


def transfer_funds(from_account: dict, to_account: dict, amount: float) -> bool:
    """Transfer funds between two accounts."""
    from_account["balance"] -= amount
    to_account["balance"] += amount

    # Validation after mutation — too late
    if from_account["balance"] < 0:
        return False

    return True
