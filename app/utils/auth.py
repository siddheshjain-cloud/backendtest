from functools import wraps
from flask import g, jsonify, current_app
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity
from app.models.user import User
import requests
import jwt as pyjwt


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            verify_jwt_in_request()
            user_id = get_jwt_identity()
            user = User.query.get(user_id)
            if not user or not user.is_admin:
                return jsonify({'error': 'Admin access required'}), 403
            return f(*args, **kwargs)
        except Exception as e:
            return jsonify({'error': 'Token is invalid'}), 401

    return decorated


def consumer_required(f):
    """Authenticate a consumer JWT and expose the database-backed user.

    This is the non-administrative analogue of ``admin_required``. It keeps
    the same legacy 401 payload for missing, invalid, or userless tokens and
    places the authenticated user on ``flask.g`` for the route and the
    entitlement-aware research service.
    """

    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            verify_jwt_in_request()
            user_id = get_jwt_identity()
            user = User.query.get(user_id)
            if not user:
                return jsonify({"error": "Token is invalid"}), 401
            g.current_user = user
            return f(*args, **kwargs)
        except Exception:
            return jsonify({"error": "Token is invalid"}), 401

    return decorated


def get_current_user():
    try:
        verify_jwt_in_request()
        user_id = get_jwt_identity()
        return User.query.get(user_id)
    except:
        return None


def verify_google_token(token):
    """Verify Google OAuth token"""
    try:
        # Get Google's public keys
        response = requests.get('https://www.googleapis.com/oauth2/v3/certs')
        keys = response.json()

        # Decode token header to get key id
        unverified_header = pyjwt.get_unverified_header(token)
        key_id = unverified_header.get('kid')

        # Find the correct key
        key = None
        for k in keys['keys']:
            if k['kid'] == key_id:
                key = k
                break

        if not key:
            return None

        # Convert key to PEM format
        public_key = pyjwt.algorithms.RSAAlgorithm.from_jwk(key)

        # Verify and decode token
        payload = pyjwt.decode(
            token,
            public_key,
            algorithms=['RS256'],
            audience=current_app.config['GOOGLE_CLIENT_ID']
        )

        return payload

    except Exception as e:
        print(f"Google token verification error: {e}")
        return None
