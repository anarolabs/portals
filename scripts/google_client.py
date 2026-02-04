#!/usr/bin/env python3
"""
Unified Google Workspace client supporting multiple projects.
Uses service account with domain-wide delegation for authentication.
No OAuth flows - just works.

Supports:
- anaro-labs: roman@anarolabs.com
- estate-mate: roman@estatemate.io

Usage:
    from google_client import get_docs_service, get_drive_service

    # Explicit project
    docs = get_docs_service(project="estate-mate")

    # Auto-detect from file path
    docs = get_docs_service(file_path="/Users/.../anaro-labs/memo.md")
"""
import sys
import warnings
from pathlib import Path

# Suppress Python 3.9 deprecation warnings from google-api-core
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Monkey-patch to suppress Google's print() calls during import
import builtins
_original_print = builtins.print
def _silent_print(*args, **kwargs):
    # Only suppress "An error occurred" messages from Google lib
    if args and "error occurred" in str(args[0]).lower():
        return
    _original_print(*args, **kwargs)
builtins.print = _silent_print

# =============================================================================
# Project configuration
# =============================================================================

PROJECT_CONFIG = {
    "anaro-labs": {
        "service_account_file": Path.home() / ".google_workspace_mcp/claude-code-api-482615-91dc5f2fd86b.json",
        "impersonate_user": "roman@anarolabs.com",
        "aliases": ["anaro-labs", "anarolabs", "anaro"],
    },
    "estate-mate": {
        "service_account_file": Path.home() / ".google_workspace_estatemate/service-account.json",
        "impersonate_user": "roman@estatemate.io",
        "aliases": ["estate-mate", "estatemate"],
    },
}

# Default project if none specified and can't auto-detect
DEFAULT_PROJECT = "anaro-labs"

# Scopes for all Google services we need
SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
]


def _detect_project_from_path(file_path: str) -> str | None:
    """Detect project from file path."""
    path_lower = file_path.lower()

    for project_name, config in PROJECT_CONFIG.items():
        for alias in config["aliases"]:
            if alias in path_lower:
                return project_name

    return None


def _resolve_project(project: str = None, file_path: str = None) -> str:
    """Resolve which project to use."""
    # 1. Explicit project takes precedence
    if project:
        # Normalize project name
        project_lower = project.lower()
        for project_name, config in PROJECT_CONFIG.items():
            if project_lower in config["aliases"]:
                return project_name

        # Unknown project
        valid = ", ".join(PROJECT_CONFIG.keys())
        print(f"ERROR: Unknown project '{project}'. Valid: {valid}", file=sys.stderr)
        sys.exit(1)

    # 2. Try to detect from file path
    if file_path:
        detected = _detect_project_from_path(file_path)
        if detected:
            return detected

    # 3. Fall back to default
    return DEFAULT_PROJECT


def _check_dependencies():
    """Check if required packages are installed."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        return True
    except ImportError:
        print("ERROR: Missing Google API dependencies", file=sys.stderr)
        print("Install with: pip install google-auth google-auth-oauthlib google-api-python-client", file=sys.stderr)
        return False


def _load_credentials(project: str):
    """Load service account credentials with domain-wide delegation."""
    config = PROJECT_CONFIG[project]
    service_account_file = config["service_account_file"]
    impersonate_user = config["impersonate_user"]

    if not service_account_file.exists():
        print(f"ERROR: Service account file not found at {service_account_file}", file=sys.stderr)
        print("Download from Google Cloud Console > IAM > Service Accounts > Keys", file=sys.stderr)
        sys.exit(1)

    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_file(
        str(service_account_file),
        scopes=SCOPES
    )

    # Impersonate the user via domain-wide delegation
    delegated_credentials = credentials.with_subject(impersonate_user)

    return delegated_credentials


def get_gmail_service(project: str = None, file_path: str = None):
    """Get authenticated Gmail API service."""
    if not _check_dependencies():
        sys.exit(1)

    from googleapiclient.discovery import build

    resolved_project = _resolve_project(project, file_path)
    credentials = _load_credentials(resolved_project)
    service = build("gmail", "v1", credentials=credentials)
    return service


def get_drive_service(project: str = None, file_path: str = None):
    """Get authenticated Google Drive API service."""
    if not _check_dependencies():
        sys.exit(1)

    from googleapiclient.discovery import build

    resolved_project = _resolve_project(project, file_path)
    credentials = _load_credentials(resolved_project)
    service = build("drive", "v3", credentials=credentials)
    return service


def get_docs_service(project: str = None, file_path: str = None):
    """Get authenticated Google Docs API service."""
    if not _check_dependencies():
        sys.exit(1)

    from googleapiclient.discovery import build

    resolved_project = _resolve_project(project, file_path)
    credentials = _load_credentials(resolved_project)
    service = build("docs", "v1", credentials=credentials)
    return service


def get_sheets_service(project: str = None, file_path: str = None):
    """Get authenticated Google Sheets API service."""
    if not _check_dependencies():
        sys.exit(1)

    from googleapiclient.discovery import build

    resolved_project = _resolve_project(project, file_path)
    credentials = _load_credentials(resolved_project)
    service = build("sheets", "v4", credentials=credentials)
    return service


def verify_setup(project: str = None):
    """Verify service account setup is working."""
    resolved_project = _resolve_project(project)
    config = PROJECT_CONFIG[resolved_project]

    print(f"Project: {resolved_project}")
    print(f"Service account file: {config['service_account_file']}")
    print(f"Impersonating user: {config['impersonate_user']}")
    print(f"Scopes: {len(SCOPES)} configured")
    print()

    if not config["service_account_file"].exists():
        print("ERROR: Service account file not found")
        return False

    try:
        # Try to get Gmail service and list labels
        service = get_gmail_service(project=resolved_project)
        results = service.users().labels().list(userId="me").execute()
        labels = results.get("labels", [])
        print(f"SUCCESS: Gmail connected - {len(labels)} labels found")

        # Try Drive
        service = get_drive_service(project=resolved_project)
        results = service.files().list(pageSize=1).execute()
        print(f"SUCCESS: Drive connected")

        # Try Docs
        service = get_docs_service(project=resolved_project)
        print(f"SUCCESS: Docs service initialized")

        # Try Sheets
        service = get_sheets_service(project=resolved_project)
        print(f"SUCCESS: Sheets service initialized")

        return True

    except Exception as e:
        print(f"ERROR: {str(e)}", file=sys.stderr)
        if "invalid_grant" in str(e).lower():
            print("\nDomain-wide delegation may not be configured correctly.", file=sys.stderr)
            print("Check: admin.google.com > Security > API Controls > Domain Wide Delegation", file=sys.stderr)
        return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Unified Google Workspace Client")
    parser.add_argument("--project", "-p", help="Project to verify (anaro-labs, estate-mate)")
    parser.add_argument("--all", "-a", action="store_true", help="Verify all projects")
    args = parser.parse_args()

    print("Unified Google Workspace Client - Setup Verification")
    print("=" * 55)

    if args.all:
        for project_name in PROJECT_CONFIG.keys():
            print(f"\n--- {project_name} ---")
            verify_setup(project=project_name)
    else:
        verify_setup(project=args.project)
