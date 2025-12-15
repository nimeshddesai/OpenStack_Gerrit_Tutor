"""
OpenStack Gerrit MCP Server

Provides tools to query OpenStack Gerrit patches:
- Patch status
- Reviewers
- Review labels
- Comments
- Progress across patchsets

Compatible with Claude Desktop using MCP + FastMCP (STUDIO mode).
"""

import sys
import traceback
import json
import pathlib
import re
from typing import Any, Dict, List, Set
import requests
import smtplib
import yaml
from email.message import EmailMessage
from datetime import datetime, timedelta
from html import escape

from fastmcp import FastMCP

# Root of all data for this MCP server
BASE_DIR = pathlib.Path(__file__).resolve().parent

GERRIT_BASE_URL = "https://review.opendev.org"
PATCH_LIST_FILE = "ibm_cinder_patches.txt"
EMAIL_CONFIG_FILE = "email_config.yaml"


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def extract_change_number(text: str) -> str | None:
    """
    Extract Gerrit numeric change number from text or URL.

    Supported examples:
    - 951829
    - https://review.opendev.org/c/openstack/cinder/+/951829
    - Any text containing the above URL

    :param text: Input text
    :return: Change number if found, else None
    """
    text = text.strip()

    if text.isdigit():
        return text

    match = re.search(r"/\+/(\d+)", text)
    if match:
        return match.group(1)

    return None


def load_allowed_patches() -> Set[str]:
    """
    Load and normalize allowed IBM Cinder patch identifiers.

    Normalization extracts numeric Gerrit change numbers
    from raw entries or URLs.

    :return: Set of normalized patch numbers
    """
    path = BASE_DIR / PATCH_LIST_FILE
    allowed: Set[str] = set()

    if not path.exists():
        return allowed

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue

        change_number = extract_change_number(line)
        if change_number:
            allowed.add(change_number)

    log("allowed: %s" % str(allowed))
    return allowed


ALLOWED_PATCHES = load_allowed_patches()


def is_ibm_patch(change_id: str) -> bool:
    """
    Check whether a patch is an allowed IBM Cinder patch.

    :param change_id: Gerrit change number or Change-Id
    :return: True if allowed, False otherwise
    """
    normalized = extract_change_number(change_id)
    if not normalized:
        return False

    return normalized in ALLOWED_PATCHES


def non_ibm_response(change_id: str) -> Dict[str, Any]:
    """
    Standard response for non-IBM patches.

    :param change_id: Gerrit change identifier
    :return: Structured rejection response
    """
    return {
        "change_id": change_id,
        "allowed": False,
        "message": "Non-IBM Cinder Driver patch. Details are restricted.",
    }


def _strip_gerrit_prefix(response_text: str) -> str:
    """
    Remove Gerrit's security prefix from JSON responses.

    Gerrit prepends: )]}'
    """
    if response_text.startswith(")]}'"):
        return response_text.split("\n", 1)[1]
    return response_text


def _get(endpoint: str) -> Any:
    """
    Perform a GET request to Gerrit REST API.

    :param endpoint: Gerrit API endpoint
    :return: Parsed JSON response
    """
    url = f"{GERRIT_BASE_URL}{endpoint}"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    clean_text = _strip_gerrit_prefix(r.text)
    return json.loads(clean_text)


def load_email_config() -> Dict[str, Any]:
    """
    Load email configuration from YAML file.

    :return: Email configuration dictionary
    """
    path = BASE_DIR / EMAIL_CONFIG_FILE
    if not path.exists():
        raise FileNotFoundError("email_config.yaml not found")

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def build_patch_summary(change_id: str) -> str:
    """
    Build a human-readable summary for a Gerrit patch.

    :param change_id: Gerrit change number
    :return: Patch summary text
    """
    data = _get(f"/changes/{change_id}/detail")

    labels = data.get("labels", {})
    label_summary = []

    for label, details in labels.items():
        approved = details.get("approved", {})
        if approved:
            label_summary.append(f"{label}: +{approved.get('value')}")
        else:
            label_summary.append(f"{label}: pending")

    return (
        f"Patch: {data.get('subject')}\n"
        f"Change ID: {change_id}\n"
        f"Status: {data.get('status')}\n"
        f"Updated: {data.get('updated')}\n"
        f"Labels: {', '.join(label_summary)}\n"
        f"Gerrit URL: {GERRIT_BASE_URL}/c/{change_id}\n"
    )


def build_patch_html_row(data: Dict[str, Any]) -> str:
    """
    Build an HTML table row for a Gerrit patch.

    :param data: Gerrit patch detail response
    :return: HTML table row
    """
    status = escape(data.get("status", "UNKNOWN"))
    subject = escape(data.get("subject", ""))
    updated = escape(data.get("updated", ""))
    change_id = data.get("change_id") or data.get("id")

    url = f"{GERRIT_BASE_URL}/c/{change_id}"

    return (
        "<tr>"
        f"<td>{subject}</td>"
        f"<td><b>{status}</b></td>"
        f"<td>{updated}</td>"
        f"<td><a href='{url}'>View</a></td>"
        "</tr>"
    )


def send_email(subject: str, text_body: str, html_body: str | None = None) -> None:
    """
    Send a multipart email (text + optional HTML).

    :param subject: Email subject
    :param text_body: Plain text body
    :param html_body: Optional HTML body
    """
    config = load_email_config()

    msg = EmailMessage()
    msg["From"] = config["email"]["from"]
    msg["To"] = ", ".join(config["email"]["to"])
    msg["Subject"] = f'{config["email"]["subject_prefix"]} {subject}'

    msg.set_content(text_body)

    if html_body:
        msg.add_alternative(html_body, subtype="html")

    smtp_cfg = config["smtp"]

    with smtplib.SMTP(smtp_cfg["host"], smtp_cfg["port"]) as server:
        if smtp_cfg.get("use_tls", False):
            server.starttls()
        server.login(smtp_cfg["username"], smtp_cfg["password"])
        server.send_message(msg)


def build_html_email(title: str, rows: str) -> str:
    """
    Build HTML email body with a table layout.

    :param title: Email title
    :param rows: HTML table rows
    :return: Full HTML document
    """
    return f"""
    <html>
      <body>
        <h2>{escape(title)}</h2>
        <table border="1" cellpadding="6" cellspacing="0">
          <tr>
            <th>Subject</th>
            <th>Status</th>
            <th>Last Updated</th>
            <th>Link</th>
          </tr>
          {rows}
        </table>
      </body>
    </html>
    """


def updated_within_last_week(updated: str) -> bool:
    """
    Check whether a patch was updated within the last 7 days.

    :param updated: Gerrit updated timestamp
    :return: True if within last week
    """
    updated_dt = datetime.fromisoformat(updated.replace("Z", ""))
    return updated_dt >= datetime.utcnow() - timedelta(days=7)


mcp = FastMCP(name="openstack-gerrit-mcp")


@mcp.tool()
def get_patch_details(change_id: str) -> Dict[str, Any]:
    """
    Fetch detailed information about a Gerrit patch.

    :param change_id: Gerrit change number or Change-Id
    :return: Patch metadata including status, owner, labels, and reviewers
    """
    try:
        if not is_ibm_patch(change_id):
            return non_ibm_response(change_id)

        data = _get(f"/changes/{change_id}/detail")

        return {
            "id": data.get("id"),
            "subject": data.get("subject"),
            "status": data.get("status"),
            "branch": data.get("branch"),
            "owner": data.get("owner", {}).get("name"),
            "updated": data.get("updated"),
            "labels": data.get("labels"),
            "reviewers": {
                "reviewers": [
                    r.get("name") for r in data.get("reviewers", {}).
                    get("REVIEWER", [])
                ]
            },
            "current_revision": data.get("current_revision"),
        }
    except Exception as e:
        log(traceback.format_exc())
        return {
            "change_id": change_id,
            "message": str(e)
        }


@mcp.tool()
def get_patch_comments(change_id: str) -> Dict[str, Any]:
    """
    Fetch all comments (inline and general) for a Gerrit patch.

    :param change_id: Gerrit change number or Change-Id
    :return: Dictionary of file-wise and general comments
    """
    if not is_ibm_patch(change_id):
        return non_ibm_response(change_id)

    comments = _get(f"/changes/{change_id}/comments")
    return {"comments": comments}


@mcp.tool()
def get_patch_progress(change_id: str) -> Dict[str, Any]:
    """
    Fetch review progress of a patch using labels and approvals.

    :param change_id: Gerrit change number or Change-Id
    :return: Review labels with approvals and scores
    """
    if not is_ibm_patch(change_id):
        return non_ibm_response(change_id)

    data = _get(f"/changes/{change_id}/detail")
    return {
        "status": data.get("status"),
        "labels": data.get("labels"),
        "submit_type": data.get("submit_type"),
        "mergeable": data.get("mergeable"),
    }


@mcp.tool()
def search_patches(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Search Gerrit patches using Gerrit query syntax.

    Example queries:
    - owner:email@domain.com
    - project:openstack/cinder status:open

    :param query: Gerrit query string
    :param limit: Max results to return
    :return: List of patch summaries
    """
    data = _get(f"/changes/?q={query}&n={limit}")

    return [
        {
            "id": change.get("id"),
            "change_id": change.get("change_id"),
            "subject": change.get("subject"),
            "status": change.get("status"),
            "updated": change.get("updated"),
        }
        for change in data
    ]


@mcp.tool()
def list_allowed_patches() -> List[str]:
    """
    List all IBM-approved Cinder patches known to this MCP server.

    :return: List of allowed patch IDs
    """
    # log(ALLOWED_PATCHES)
    return sorted(ALLOWED_PATCHES)


@mcp.tool()
def send_summary_email(subject: str, email_body: str, html_body: str) \
        -> Dict[str, Any]:
    """
    Send a summary email for using the given subject and content.

    :param subject: Email subject
    :param email_body: Email body (Text)
    :param html_body: Email body(html)
    :return: Email dispatch status
    """

    send_email(subject=subject, text_body=email_body, html_body=html_body)

    return {
        "sent": True,
        "subject": subject
    }


@mcp.tool()
def send_patch_summary_email(change_ids: List[str]) -> Dict[str, Any]:
    """
    Send a summary email for allowed IBM Cinder patches.

    :param change_ids: List of Gerrit change numbers or URLs
    :return: Email dispatch status
    """
    summaries = []
    skipped = []

    for raw_id in change_ids:
        normalized = extract_change_number(raw_id)
        if not normalized or not is_ibm_patch(normalized):
            skipped.append(raw_id)
            continue

        summaries.append(build_patch_summary(normalized))

    if not summaries:
        return {
            "sent": False,
            "message": "No IBM Cinder patches found. Email not sent.",
            "skipped": skipped,
        }

    email_body = "\n\n".join(summaries)
    send_email(subject="Patch Summary", text_body=email_body)

    return {
        "sent": True,
        "included_patches": len(summaries),
        "skipped": skipped,
    }


@mcp.tool()
def send_weekly_digest_email() -> Dict[str, Any]:
    """
    Generate and send a weekly HTML email digest
    for IBM-approved Cinder patches.

    :return: Digest dispatch status
    """
    rows = []
    text_lines = []

    for change_id in sorted(ALLOWED_PATCHES):
        data = _get(f"/changes/{change_id}/detail")

        if not updated_within_last_week(data.get("updated", "")):
            continue

        rows.append(build_patch_html_row(data))
        text_lines.append(
            f"{data.get('subject')} | {data.get('status')} | "
            f"{GERRIT_BASE_URL}/c/{change_id}"
        )

    if not rows:
        return {
            "sent": False,
            "message": "No IBM Cinder patches updated in the last week.",
        }

    html_body = build_html_email(
        title="Weekly IBM Cinder Gerrit Digest",
        rows="".join(rows),
    )

    send_email(
        subject="Weekly Patch Digest",
        text_body="\n".join(text_lines),
        html_body=html_body,
    )

    return {
        "sent": True,
        "patches_included": len(rows),
    }


# app = mcp.sse_app()

if __name__ == "__main__":
    mcp.run()
