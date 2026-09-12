"""Administrative research API blueprints.

``admin_research_bp`` owns the /api/admin/research routes and
``admin_entitlements_bp`` owns the /api/admin/users entitlement routes added by
later Plan 5 tasks. This module only defines the blueprints for registration.
"""

from flask import Blueprint


admin_research_bp = Blueprint("admin_research", __name__)
admin_entitlements_bp = Blueprint("admin_entitlements", __name__)
