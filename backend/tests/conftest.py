import os
os.environ["DATABASE_URL"]="sqlite://"
os.environ["AUTOSRE_API_KEY"]="test-key"
os.environ["FAILURE_THRESHOLD"]="2"
import pytest
from app.database import Base, engine

@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine); yield

@pytest.fixture
def auth(): return {"X-API-Key":"test-key"}

