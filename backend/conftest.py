"""Root conftest — sets environment variables needed by Settings() before any app import."""

import os

# Minimal env vars so that Settings() can instantiate without real secrets/keys.
# These are set before any app module is imported via pytest's conftest loading order.
_TEST_ENV = {
    "JWT_ALGORITHM": "HS256",
    "JWT_SECRET": "test-secret-key-for-unit-tests-minimum-32-characters!",
    "JWT_ACCESS_TOKEN_MINUTES": "60",
    "JWT_REFRESH_TOKEN_DAYS": "7",
    "POSTGRES_USER": "test",
    "POSTGRES_PASSWORD": "test",
    "POSTGRES_HOST": "localhost",
    "POSTGRES_DB": "test_db",
    "POSTGRES_PORT": "5432",
    "AWS_REGION": "us-east-1",
    "AWS_BEDROCK_SONNET_ARN": "arn:aws:bedrock:us-east-1::foundation-model/test",
    "AWS_BEARER_TOKEN_BEDROCK": "test-token",
    "ENVIRONMENT": "development",
    "REDIS_URL": "redis://localhost:6379/0",
    "NEO4J_URI": "bolt://localhost:7687",
    "NEO4J_USER": "neo4j",
    "NEO4J_PASSWORD": "test",
}

for key, value in _TEST_ENV.items():
    os.environ.setdefault(key, value)
