"""SQLAlchemy declarative base and metadata.

Provides the shared ``Base`` class and ``metadata`` instance used by all
ORM models in the application.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import declarative_base


metadata = MetaData()

Base = declarative_base(metadata=metadata)
