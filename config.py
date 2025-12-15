# config.py
import pathlib

# Root of all data for this MCP server
BASE_DIR = pathlib.Path(__file__).resolve().parent

GERRIT_BASE_URL = "https://review.opendev.org"
PATCH_LIST_FILE = "ibm_cinder_patches.txt"
EMAIL_CONFIG_FILE = "email_config.yaml"
