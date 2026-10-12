"""Continuous Monitoring V1 follow-up: ``GET /api/admin/alerts``.

Admin-only, read-only. Reuses ``AlertService`` exactly as every other
consumer of it does -- this route adds no new write path and no new
alert-recording logic, only a feed over what ``AlertService`` already
records.
"""

from flask import Blueprint, jsonify, request

from app.schemas.admin_alerts import AlertEventListSchema, load_alert_list_query
from app.services.alert_service import AlertService
from app.utils.auth import admin_required
from app.utils.research_errors import research_error_boundary

admin_alerts_bp = Blueprint("admin_alerts", __name__)


@admin_alerts_bp.route("/", methods=["GET"])
@admin_required
@research_error_boundary
def list_recent_alerts():
    query = load_alert_list_query(request.args)
    events = AlertService.list_recent_events(limit=query["limit"])
    return jsonify(AlertEventListSchema().dump({"items": events})), 200
