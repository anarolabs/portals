"""Google Docs adapter with direct API access."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime
from typing import Any

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from portals.adapters.base import DocumentAdapter, PlatformURI, RemoteMetadata
from portals.adapters.gdocs.converter import GoogleDocsConverter
from portals.core.exceptions import AdapterError
from portals.core.models import Document, DocumentMetadata


SCOPES = [
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/drive.file'  # Access to files created/opened by app
]


class GoogleDocsAdapter(DocumentAdapter):
    """Adapter for Google Docs with full formatting support.

    Uses direct Google Docs API access for full control over formatting,
    including heading styles and native lists.
    """

    def __init__(
        self,
        credentials_path: str | None = None,
        token_path: str | None = None,
        account: str | None = None
    ):
        """Initialize Google Docs adapter.

        Args:
            credentials_path: Path to OAuth2 credentials file
            token_path: Path to store OAuth2 token
            account: Account identifier (email or alias: 'personal', 'consultancy', 'estatemate')
        """
        # Account aliases for convenience
        account_emails = {
            'personal': 'rsiepelmeyer@gmail.com',
            'consultancy': 'roman@anarolabs.com',
            'estatemate': 'roman@estatemate.io',
        }

        # Resolve account alias to email
        if account:
            account_email = account_emails.get(account, account)
            # Use account-specific token file
            token_filename = f"google_token_{account_email.replace('@', '_at_').replace('.', '_')}.json"
        else:
            # Default account (personal)
            account_email = 'rsiepelmeyer@gmail.com'
            token_filename = "google_token.json"

        self.account = account_email
        self.credentials_path = credentials_path or os.path.expanduser(
            "~/.config/docsync/google_credentials.json"
        )
        self.token_path = token_path or os.path.expanduser(
            f"~/.config/docsync/{token_filename}"
        )

        self.converter = GoogleDocsConverter()
        self._service = None
        self._drive_service = None

    @property
    def service(self):
        """Lazy-load Google Docs service."""
        if self._service is None:
            creds = self._get_credentials()
            self._service = build('docs', 'v1', credentials=creds)
        return self._service

    @property
    def drive_service(self):
        """Lazy-load Google Drive service."""
        if self._drive_service is None:
            creds = self._get_credentials()
            self._drive_service = build('drive', 'v3', credentials=creds)
        return self._drive_service

    def _get_credentials(self) -> Credentials:
        """Get Google API credentials.

        Returns:
            Credentials object
        """
        creds = None

        # Load existing token
        if os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)

        # Refresh or create new credentials
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(self.credentials_path):
                    raise FileNotFoundError(
                        f"Google credentials not found at {self.credentials_path}. "
                        "Please set up OAuth2 credentials."
                    )

                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path, SCOPES
                )
                creds = flow.run_local_server(port=0)

            # Save credentials
            os.makedirs(os.path.dirname(self.token_path), exist_ok=True)
            with open(self.token_path, 'w') as token:
                token.write(creds.to_json())

        return creds

    async def read(self, uri: str) -> Document:
        """Read document from Google Docs.

        Args:
            uri: Google Docs URI (e.g., "gdocs://doc-id")

        Returns:
            Document object with content and metadata
        """
        try:
            parsed_uri = self.parse_uri(uri)
            doc_id = parsed_uri.identifier

            # Get document
            doc = self.service.documents().get(documentId=doc_id).execute()

            # Extract content (simplified - just plain text for now)
            content = self._extract_content(doc)

            # Get metadata from Drive
            drive_file = self.drive_service.files().get(
                fileId=doc_id,
                fields='id,name,modifiedTime,createdTime'
            ).execute()

            # Parse timestamps
            modified_time = datetime.fromisoformat(
                drive_file.get('modifiedTime', datetime.now().isoformat()).replace('Z', '+00:00')
            )
            created_time = datetime.fromisoformat(
                drive_file.get('createdTime', datetime.now().isoformat()).replace('Z', '+00:00')
            )

            metadata = DocumentMetadata(
                title=doc.get('title', ''),
                created_at=created_time,
                modified_at=modified_time,
            )

            return Document(content=content, metadata=metadata)

        except HttpError as e:
            raise AdapterError(f"Failed to read Google Doc {uri}: {e}") from e

    async def write(self, uri: str, doc: Document) -> None:
        """Write document to Google Docs with full formatting.

        Args:
            uri: Google Docs URI
            doc: Document to write
        """
        try:
            parsed_uri = self.parse_uri(uri)
            doc_id = parsed_uri.identifier

            # Get current document
            gdoc = self.service.documents().get(documentId=doc_id).execute()
            doc_length = gdoc['body']['content'][-1]['endIndex'] - 1

            # Convert markdown
            result = self.converter.markdown_to_gdocs(doc.content)

            # Build requests
            requests = []

            # 1. Delete existing content (cannot include final newline at doc_length)
            if doc_length > 1:
                requests.append({
                    'deleteContentRange': {
                        'range': {
                            'startIndex': 1,
                            'endIndex': doc_length
                        }
                    }
                })

            # 2. Insert new plain text
            requests.append({
                'insertText': {
                    'location': {'index': 1},
                    'text': result.plain_text
                }
            })

            # 3. Apply formatting
            requests.extend(self.converter.generate_batch_requests(result))

            # Execute batch update
            self.service.documents().batchUpdate(
                documentId=doc_id,
                body={'requests': requests}
            ).execute()

            # Update title if changed
            if doc.metadata.title:
                self.drive_service.files().update(
                    fileId=doc_id,
                    body={'name': doc.metadata.title}
                ).execute()

        except HttpError as e:
            raise AdapterError(f"Failed to write Google Doc {uri}: {e}") from e

    async def get_metadata(self, uri: str) -> RemoteMetadata:
        """Get document metadata without reading full content.

        Args:
            uri: Google Docs URI

        Returns:
            RemoteMetadata with hash and timestamp
        """
        try:
            parsed_uri = self.parse_uri(uri)
            doc_id = parsed_uri.identifier

            # Get metadata from Drive
            drive_file = self.drive_service.files().get(
                fileId=doc_id,
                fields='id,modifiedTime,md5Checksum'
            ).execute()

            # Use MD5 from Drive, or generate from modified time
            content_hash = drive_file.get('md5Checksum', '')
            if not content_hash:
                content_hash = hashlib.md5(
                    drive_file.get('modifiedTime', '').encode()
                ).hexdigest()

            return RemoteMetadata(
                uri=uri,
                content_hash=content_hash,
                last_modified=drive_file.get('modifiedTime', ''),
                exists=True
            )

        except HttpError as e:
            if e.resp.status == 404:
                return RemoteMetadata(
                    uri=uri,
                    content_hash='',
                    last_modified='',
                    exists=False
                )
            raise AdapterError(f"Failed to get metadata for {uri}: {e}") from e

    async def exists(self, uri: str) -> bool:
        """Check if document exists.

        Args:
            uri: Google Docs URI

        Returns:
            True if document exists
        """
        try:
            parsed_uri = self.parse_uri(uri)
            doc_id = parsed_uri.identifier

            self.drive_service.files().get(fileId=doc_id, fields='id').execute()
            return True

        except HttpError as e:
            if e.resp.status == 404:
                return False
            raise AdapterError(f"Failed to check existence of {uri}: {e}") from e

    def parse_uri(self, uri: str) -> PlatformURI:
        """Parse Google Docs URI.

        Args:
            uri: URI string (e.g., "gdocs://doc-id" or just "doc-id")

        Returns:
            Parsed PlatformURI object
        """
        if uri.startswith("gdocs://"):
            doc_id = uri[8:]
        elif uri.startswith("https://docs.google.com/document/d/"):
            # Extract doc ID from full URL
            doc_id = uri.split("/document/d/")[1].split("/")[0]
        else:
            # Assume it's just the doc ID
            doc_id = uri

        return PlatformURI(
            platform="gdocs",
            identifier=doc_id,
            raw_uri=f"gdocs://{doc_id}"
        )

    async def create(self, uri: str, doc: Document, parent_id: str | None = None) -> str:
        """Create new Google Doc with formatting.

        Args:
            uri: URI (title will be used)
            doc: Document to create
            parent_id: Optional folder ID

        Returns:
            Full URI of created document
        """
        try:
            # Create empty document
            gdoc = self.service.documents().create(
                body={'title': doc.metadata.title or 'Untitled'}
            ).execute()
            doc_id = gdoc['documentId']

            # Convert markdown
            result = self.converter.markdown_to_gdocs(doc.content)

            # Build requests
            requests = []

            # 1. Insert plain text
            requests.append({
                'insertText': {
                    'location': {'index': 1},
                    'text': result.plain_text
                }
            })

            # 2. Apply formatting
            requests.extend(self.converter.generate_batch_requests(result))

            # Execute batch update
            self.service.documents().batchUpdate(
                documentId=doc_id,
                body={'requests': requests}
            ).execute()

            # Move to folder if specified
            if parent_id:
                self.drive_service.files().update(
                    fileId=doc_id,
                    addParents=parent_id,
                    fields='id,parents'
                ).execute()

            return f"gdocs://{doc_id}"

        except HttpError as e:
            raise AdapterError(f"Failed to create Google Doc: {e}") from e

    async def delete(self, uri: str) -> None:
        """Delete document from Google Docs.

        Args:
            uri: Google Docs URI
        """
        try:
            parsed_uri = self.parse_uri(uri)
            doc_id = parsed_uri.identifier

            self.drive_service.files().delete(fileId=doc_id).execute()

        except HttpError as e:
            raise AdapterError(f"Failed to delete Google Doc {uri}: {e}") from e

    def _extract_content(self, doc: dict[str, Any]) -> str:
        """Extract plain text content from Google Doc.

        Args:
            doc: Document object from API

        Returns:
            Plain text content
        """
        content = []

        for element in doc.get('body', {}).get('content', []):
            if 'paragraph' in element:
                para = element['paragraph']
                for el in para.get('elements', []):
                    if 'textRun' in el:
                        content.append(el['textRun']['content'])

        return ''.join(content)
