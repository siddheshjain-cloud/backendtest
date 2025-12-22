import os
import logging
import random
import string
from datetime import datetime, timezone, timedelta
from typing import Optional
import requests
from app import db
from app.models.user import User
from app.models.telegram_verification import TelegramVerification
from app.models.utils import TradeStatus

logger = logging.getLogger(__name__)


class TelegramService:
    def __init__(self):
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    def send_message(self, chat_id: str, text: str, parse_mode: str = 'HTML') -> bool:
        """Send a message via Telegram bot"""
        try:
            url = f"{self.base_url}/sendMessage"
            payload = {
                'chat_id': chat_id,
                'text': text,
                'parse_mode': parse_mode
            }
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    def generate_verification_code(self) -> str:
        """Generate a random 8-character verification code"""
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

    def create_verification(self, user_id: str) -> Optional[TelegramVerification]:
        """Create a new verification code for user"""
        try:
            # Delete any existing unverified codes for this user
            TelegramVerification.query.filter_by(
                user_id=user_id,
                verified=False
            ).delete()

            # Generate new code
            code = self.generate_verification_code()
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)

            verification = TelegramVerification(
                user_id=user_id,
                verification_code=code,
                expires_at=expires_at
            )

            db.session.add(verification)
            db.session.commit()

            return verification
        except Exception as e:
            logger.error(f"Failed to create verification: {e}")
            db.session.rollback()
            return None

    def verify_code(self, code: str, chat_id: str, username: str = None) -> Optional[User]:
        """Verify code and link Telegram account to user"""
        try:
            # Find verification code
            verification = TelegramVerification.query.filter_by(
                verification_code=code,
                verified=False
            ).first()

            if not verification:
                return None

            # Check if expired
            if datetime.now(timezone.utc) > verification.expires_at.replace(tzinfo=timezone.utc):
                return None

            # Get user
            user = User.query.get(verification.user_id)
            if not user:
                return None

            # Update user with Telegram info
            user.telegram_chat_id = chat_id
            user.telegram_username = username
            user.telegram_enabled = True
            user.telegram_connected_at = datetime.now(timezone.utc)

            # Mark verification as complete
            verification.verified = True

            db.session.commit()

            return user
        except Exception as e:
            logger.error(f"Failed to verify code: {e}")
            db.session.rollback()
            return None

    def disconnect_telegram(self, user_id: str) -> bool:
        """Disconnect Telegram from user account"""
        try:
            user = User.query.get(user_id)
            if not user:
                return False

            user.telegram_chat_id = None
            user.telegram_username = None
            user.telegram_enabled = False
            user.telegram_connected_at = None

            db.session.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to disconnect Telegram: {e}")
            db.session.rollback()
            return False

    def send_trade_alert(self, user: User, trade) -> bool:
        """Send trade alert notification to user"""
        if not user.telegram_enabled or not user.telegram_chat_id:
            return False

        # Format message based on alert type
        if trade.status == TradeStatus.ENTRY:
            emoji = '✅'
            title = 'Entry Hit'
            price = trade.entry
        elif trade.status == TradeStatus.STOPLOSS:
            emoji = '🛑'
            title = 'Stop Loss Hit'
            price = trade.stoploss
        elif trade.status == TradeStatus.TARGET:
            emoji = '🎯'
            title = 'Target Hit'
            price = trade.target
        else:
            return False

        message = f"""
        {emoji} <b>{title}!</b>
        
        <b>Symbol:</b> {trade.symbol}
        <b>Side:</b> {trade.side}
        <b>Price:</b> ₹{price:.2f}
        <b>Current Price:</b> ₹{trade.last_price:.2f}
        
        <b>Entry:</b> ₹{trade.entry:.2f}
        {f'<b>Stop Loss:</b> ₹{trade.stoploss:.2f}' if trade.stoploss else ''}
        {f'<b>Target:</b> ₹{trade.target:.2f}' if trade.target else ''}
        
        {f'<b>Notes:</b> {trade.notes}' if trade.notes else ''}
        """

        return self.send_message(user.telegram_chat_id, message.strip())

    def send_welcome_message(self, chat_id: str, user_name: str) -> bool:
        """Send welcome message after successful connection"""
        message = f"""
        🎉 <b>Welcome, {user_name}!</b>
        
        Your Telegram account has been successfully connected to Stock Price Alert.
        
        You will now receive notifications when your trade alerts trigger.
        
        <b>Alert Types:</b>
        🎯 Entry Hit
        🛑 Stop Loss Hit
        ✅ Target Hit
        
        You can manage your notification settings from the app.
        """
        return self.send_message(chat_id, message.strip())


# Singleton instance
telegram_service = TelegramService()