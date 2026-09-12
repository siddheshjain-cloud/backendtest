"""Consumer research API blueprint.

Routes are added by later Plan 5 tasks; this module only defines the blueprint
so the application factory can register the /api/research boundary.
"""

from flask import Blueprint


research_bp = Blueprint("research", __name__)
