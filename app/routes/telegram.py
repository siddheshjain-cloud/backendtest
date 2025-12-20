import os
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from app.utils.auth import get_current_user
from app.services.telegram_service import telegram_service

telegram_bp = Blueprint('telegram', __name__)


@telegram_bp.route('/generate-code', methods=['POST'])
@jwt_required()
def generate_verification_code():
    """Generate verification code for Telegram linking"""
    current_user = get_current_user()

    if current_user.telegram_enabled:
        return jsonify({
            'error': 'Telegram already connected'
        }), 400

    verification = telegram_service.create_verification(current_user.id)

    if not verification:
        return jsonify({
            'error': 'Failed to generate verification code'
        }), 500

    return jsonify({
        'verification_code': verification.verification_code,
        'expires_at': verification.expires_at.isoformat(),
        'bot_username': os.getenv('TELEGRAM_BOT_USERNAME', 'your_bot')
    })


@telegram_bp.route('/disconnect', methods=['POST'])
@jwt_required()
def disconnect_telegram():
    """Disconnect Telegram from user account"""
    current_user = get_current_user()

    if not current_user.telegram_enabled:
        return jsonify({
            'error': 'Telegram not connected'
        }), 400

    success = telegram_service.disconnect_telegram(current_user.id)

    if not success:
        return jsonify({
            'error': 'Failed to disconnect Telegram'
        }), 500

    return jsonify({
        'message': 'Telegram disconnected successfully'
    })


@telegram_bp.route('/status', methods=['GET'])
@jwt_required()
def get_telegram_status():
    """Get current Telegram connection status"""
    current_user = get_current_user()

    return jsonify({
        'connected': current_user.telegram_enabled,
        'username': current_user.telegram_username,
        'connected_at': current_user.telegram_connected_at.isoformat() if current_user.telegram_connected_at else None
    })


@telegram_bp.route('/webhook', methods=['POST'])
def telegram_webhook():
    """Webhook endpoint for Telegram bot updates"""
    try:
        update = request.json

        # Handle /start command
        if 'message' in update:
            message = update['message']
            chat_id = str(message['chat']['id'])
            text = message.get('text', '')
            username = message['from'].get('username')

            if text.startswith('/start'):
                # Check if verification code is provided
                parts = text.split()
                if len(parts) > 1:
                    code = parts[1]
                    user = telegram_service.verify_code(code, chat_id, username)

                    if user:
                        telegram_service.send_welcome_message(chat_id, user.name)
                    else:
                        telegram_service.send_message(
                            chat_id,
                            "❌ Invalid or expired verification code. Please generate a new code from the app."
                        )
                else:
                    telegram_service.send_message(
                        chat_id,
                        "👋 Welcome to Stock Price Alert Bot!\n\n"
                        "To connect your account:\n"
                        "1. Generate a verification code from the app\n"
                        "2. Send /start CODE to this bot"
                    )

        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False}), 500