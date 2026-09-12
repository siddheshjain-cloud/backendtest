"""Administrative document API blueprint.

Routes are added by later Plan 5 tasks; this module only defines the blueprint
so the application factory can register the shared /api/admin/research
boundary.
"""

from flask import Blueprint


admin_documents_bp = Blueprint("admin_documents", __name__)
