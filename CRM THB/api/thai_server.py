"""API-only launch of the same CRM application."""
from bot_app import run_app

if __name__=='__main__':
    run_app(api_only=True)
