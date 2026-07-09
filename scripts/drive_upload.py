#!/usr/bin/env python3
"""Upload a local file to Google Drive (multi-project).

Uses the central credential resolution in google_client.py, so uploads land
under the impersonated workspace identity (roman@anarolabs.com or
roman@estatemate.io), never the raw service account.

Usage:
    drive_upload.py --file PATH [--name NAME] [--folder FOLDER_ID] --project {anaro-labs|estate-mate}
"""
import argparse
import mimetypes
import os
import sys

from google_client import get_drive_service, get_impersonate_user


def main():
    parser = argparse.ArgumentParser(description="Upload a file to Google Drive (multi-project)")
    parser.add_argument("--file", required=True, help="Local file path to upload")
    parser.add_argument("--name", help="Drive filename (defaults to local basename)")
    parser.add_argument("--folder", help="Destination folder ID (defaults to My Drive root)")
    parser.add_argument("--project", default="anaro-labs", help="anaro-labs or estate-mate")
    args = parser.parse_args()

    if not os.path.isfile(args.file):
        sys.exit(f"File not found: {args.file}")

    from googleapiclient.http import MediaFileUpload

    name = args.name or os.path.basename(args.file)
    mime = mimetypes.guess_type(args.file)[0] or "application/octet-stream"
    metadata = {"name": name}
    if args.folder:
        metadata["parents"] = [args.folder]

    service = get_drive_service(project=args.project)
    media = MediaFileUpload(args.file, mimetype=mime, resumable=True)
    created = (
        service.files()
        .create(body=metadata, media_body=media, fields="id,name,webViewLink", supportsAllDrives=True)
        .execute()
    )
    print(f"Uploaded as {get_impersonate_user(args.project)}")
    print(f"Name: {created['name']}")
    print(f"ID:   {created['id']}")
    print(f"Link: {created['webViewLink']}")


if __name__ == "__main__":
    main()
