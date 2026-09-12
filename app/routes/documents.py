"""Consumer document API blueprint.

Routes are added by later Plan 5 tasks; this module only defines the blueprint
so the application factory can register the shared /api/research boundary.
"""

from flask import Blueprint


documents_bp = Blueprint("documents", __name__)
