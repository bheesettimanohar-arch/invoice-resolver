import os
from google.adk.cli.fast_api import get_fast_api_app

# Locate the agent source directory (sibling 'app' folder)
agents_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))

# Initialize the FastAPI app with an in-memory SQLite database for session storage on Vercel
app = get_fast_api_app(
    agents_dir=agents_dir,
    session_service_uri="sqlite+aiosqlite:///:memory:",
    allow_origins=["*"],
    web=True
)
