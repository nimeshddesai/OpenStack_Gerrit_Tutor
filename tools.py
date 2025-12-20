"""
Tools for OpenStack Gerrit MCP Server
"""
import sys
import smtplib
import json
import re
from typing import Any, Dict, Set, List
from urllib.parse import quote
from email.message import EmailMessage
from datetime import datetime, timedelta
from html import escape
import yaml
import requests


from config import BASE_DIR, GERRIT_BASE_URL, PATCH_LIST_FILE, \
    EMAIL_CONFIG_FILE


def log(msg):
    """
    print log message
    """
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

    log(f"allowed: {allowed}")
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


def get(endpoint: str) -> Any:
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
    data = get(f"/changes/{change_id}/detail")

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


def send_email(subject: str, text_body: str, html_body: str | None = None) \
        -> None:
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


def get_change_with_revision(change_id: str) -> dict:
    """
    Fetch change details including current revision.

    :param change_id: Gerrit change number
    :return: Change data with revision info
    """
    return get(
        f"/changes/{change_id}/detail?o=CURRENT_REVISION"
    )


def get_current_revision(change_id: str) -> str:
    """
    Fetch the current revision ID for a Gerrit patch.

    Gerrit requires explicit options to return revision data.

    :param change_id: Gerrit change number
    :return: Current revision ID
    """

    change = get_change_with_revision(change_id)
    revision = change["current_revision"]
    if not revision:
        raise ValueError(
            f"Current revision not found for patch {change_id}"
        )

    return revision


def review_diff(diff_text: str) -> List[str]:
    """
    Perform basic heuristic review on a diff.

    :param diff_text: Unified diff text
    :return: List of review comments
    """
    comments = []

    if "print(" in diff_text:
        comments.append("Avoid using print(); use logging instead.")

    if "TODO" in diff_text:
        comments.append("Found TODO comment; ensure it is addressed.")

    if "except Exception" in diff_text:
        comments.append(
            "Avoid catching broad Exception; catch specific exceptions."
        )

    if "pass\n" in diff_text:
        comments.append(
            "Found 'pass' statement; ensure this is intentional."
        )

    return comments


def decode_diff_content(content: list) -> str:
    """
    Decode Gerrit diff content blocks into readable text.

    :param content: Gerrit diff 'content' array
    :return: Unified diff text
    """
    lines = []

    for block in content:
        if "ab" in block:
            lines.extend(block["ab"])

        elif "a" in block:
            lines.extend(f"-{line}" for line in block["a"])

        elif "b" in block:
            lines.extend(f"+{line}" for line in block["b"])

        # skip blocks are ignored

    return "".join(lines)


def fetch_file_diff(
    change_id: str,
    revision: str,
    file_path: str,
) -> str:
    """
    Fetch and decode unified diff for a Gerrit patch file.

    :param change_id: Gerrit change number
    :param revision: Revision SHA
    :param file_path: File path in patch
    :return: Decoded diff text
    """
    encoded_path = quote(file_path, safe="")

    diff = get(
        f"/changes/{change_id}/revisions/{revision}"
        f"/files/{encoded_path}/diff"
    )

    diff_text = decode_diff_content(diff.get("content", []))
    return diff_text
