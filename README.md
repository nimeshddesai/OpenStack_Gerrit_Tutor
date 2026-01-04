# Architecture
```
Claude Desktop
      │
      │ MCP (STUDIO mode)
      ▼
MCP Server
      │
      ├─ Gerrit REST API (review.opendev.org)
```
Claude communicates with the MCP server using Model Context Protocol (STUDIO mode).
The MCP server fetches and processes Gerrit data and returns structured, governed results to Claude.

# Project Structure
```
ibm-cinder-mcp/
├── main.py                 # MCP entry point & tool definitions
├── tools.py                # Helper logic (Gerrit, docs, repo, box)
├── config.py               # Central configuration
│
├── patches.txt             # Approved patch allowlist
├── email_config.yaml       # SMTP & email configuration
├── requirements.txt        # python dependencies
│
└── README.md
```

# Prerequisites

- Python 3.10+
- Claude Desktop (Mac) - https://claude.com/download
- Network access to review.opendev.org

# Setup
- python3 -m venv .venv
- source .venv/bin/activate
- pip install -r requirements.txt

# Add MCP Configuration
local config - ~/Library/Application Support/Claude/claude_desktop_config.json
```
{
  "mcpServers": {
    "ibm_cinder_mcp": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": [
        "-u",
        "/absolute/path/to/ibm-cinder-mcp/main.py"
      ],
      "env": {
        "PYTHONUNBUFFERED": "1"
      }
    }
  }
}
```

Update patches.txt

Restart Claude Desktop after updating.
