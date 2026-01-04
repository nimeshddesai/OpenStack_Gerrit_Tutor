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

import traceback
from typing import Any, Dict, List

from fastmcp import FastMCP

from config import GERRIT_BASE_URL
import tools


mcp = FastMCP(name="openstack-gerrit-mcp")


@mcp.tool()
def get_patch_details(change_id: str) -> Dict[str, Any]:
    """
    Fetch detailed information about a Gerrit patch.

    :param change_id: Gerrit change number or Change-Id
    :return: Patch metadata including status, owner, labels, and reviewers
    """
    try:
        if not tools.is_ibm_patch(change_id):
            return tools.non_ibm_response(change_id)

        data = tools.get(f"/changes/{change_id}/detail")

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
        tools.log(traceback.format_exc())
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
    if not tools.is_ibm_patch(change_id):
        return tools.non_ibm_response(change_id)

    comments = tools.get(f"/changes/{change_id}/comments")
    return {"comments": comments}


@mcp.tool()
def get_patch_progress(change_id: str) -> Dict[str, Any]:
    """
    Fetch review progress of a patch using labels and approvals.

    :param change_id: Gerrit change number or Change-Id
    :return: Review labels with approvals and scores
    """
    if not tools.is_ibm_patch(change_id):
        return tools.non_ibm_response(change_id)

    data = tools.get(f"/changes/{change_id}/detail")
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
    data = tools.get(f"/changes/?q={query}&n={limit}")

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
    return sorted(tools.ALLOWED_PATCHES)


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

    tools.send_email(subject=subject, text_body=email_body,
                     html_body=html_body)

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
        normalized = tools.extract_change_number(raw_id)
        if not normalized or not tools.is_ibm_patch(normalized):
            skipped.append(raw_id)
            continue

        summaries.append(tools.build_patch_summary(normalized))

    if not summaries:
        return {
            "sent": False,
            "message": "No IBM Cinder patches found. Email not sent.",
            "skipped": skipped,
        }

    email_body = "\n\n".join(summaries)
    tools.send_email(subject="Patch Summary", text_body=email_body)

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

    for change_id in sorted(tools.ALLOWED_PATCHES):
        data = tools.get(f"/changes/{change_id}/detail")

        if not tools.updated_within_last_week(data.get("updated", "")):
            continue

        rows.append(tools.build_patch_html_row(data))
        text_lines.append(
            f"{data.get('subject')} | {data.get('status')} | "
            f"{GERRIT_BASE_URL}/c/{change_id}"
        )

    if not rows:
        return {
            "sent": False,
            "message": "No IBM Cinder patches updated in the last week.",
        }

    html_body = tools.build_html_email(
        title="Weekly IBM Cinder Gerrit Digest",
        rows="".join(rows),
    )

    tools.send_email(
        subject="Weekly Patch Digest",
        text_body="\n".join(text_lines),
        html_body=html_body,
    )

    return {
        "sent": True,
        "patches_included": len(rows),
    }


@mcp.tool()
def list_patch_files(change_id: str) -> Dict[str, Any]:
    """
    List files modified in an IBM Cinder Gerrit patch.

    :param change_id: Gerrit change number or URL
    :return: List of modified files
    """
    normalized = tools.extract_change_number(change_id)
    tools.log(f'normalized: {normalized}')
    if not normalized or not tools.is_ibm_patch(normalized):
        return tools.non_ibm_response(change_id)

    revision = tools.get_current_revision(normalized)
    tools.log(f'revision: {revision}')
    files = tools.get(
        f"/changes/{normalized}/revisions/{revision}/files/"
    )

    return {
        "allowed": True,
        "patch": normalized,
        "files": list(files.keys()),
    }


@mcp.tool()
def review_patch_code(change_id: str) -> Dict[str, Any]:
    """
    Perform automated review of code changes in an IBM Cinder patch.

    :param change_id: Gerrit change number or URL
    :return: Review findings per file
    """
    normalized = tools.extract_change_number(change_id)
    if not normalized or not tools.is_ibm_patch(normalized):
        return tools.non_ibm_response(change_id)

    revision = tools.get_current_revision(normalized)
    files = tools.get(
        f"/changes/{normalized}/revisions/{revision}/files/"
    )

    review_results = {}

    for file_path in files.keys():
        if file_path.startswith("/"):
            continue

        diff_text = tools.fetch_file_diff(
            change_id=normalized,
            revision=revision,
            file_path=file_path,
        )

        comments = tools.review_diff(diff_text)

        if comments:
            review_results[file_path] = comments

    return {
        "allowed": True,
        "patch": normalized,
        "issues_found": len(review_results),
        "review": review_results,
    }


# app = mcp.sse_app()

if __name__ == "__main__":
    mcp.run()
