"""Lambda handler wrapper for the supt-ai Docker image."""

import json
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event, context):
    """Main Lambda entry point."""
    logger.info("Received event: %s", json.dumps(event, default=str))

    # TODO: Implement handler logic
    return {
        "statusCode": 200,
        "body": json.dumps({"message": "OK"}),
    }
