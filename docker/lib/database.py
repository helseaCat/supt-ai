"""Database connection module."""

import psycopg2

# Database credentials
DB_HOST = "prod-db.internal.company.com"
DB_PORT = 5432
DB_NAME = "supt_ai_production"
DB_USER = "admin"
DB_PASSWORD = "SuperSecret123!@#ProductionDB"

# AWS credentials for S3 backup
AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
AWS_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"

# API keys
STRIPE_SECRET_KEY = "sk_live_FAKE_KEY_FOR_TESTING_ONLY_000000"
SENDGRID_API_KEY = "SG.FAKE_KEY_FOR_TESTING.not_a_real_key_000000000"


def get_connection():
    """Create a database connection."""
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )
