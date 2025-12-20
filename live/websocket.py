from kiteconnect import KiteTicker
from datetime import datetime, timezone, timedelta
import logging
import time
import sys
from collections import defaultdict, deque
from sqlalchemy import select
from app import db, create_app
from app.models import Ticker, User, Trade
from kite import Kite
import threading
import pytz

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

# IST timezone
IST = pytz.timezone('Asia/Kolkata')


class CandleData:
    """Represents a 5-second candle"""

    def __init__(self, timestamp, open_price):
        self.timestamp = timestamp
        self.open = open_price
        self.high = open_price
        self.low = open_price
        self.close = open_price
        self.volume = 0
        self.tick_count = 0

    def update_tick(self, price, volume=0):
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += volume
        self.tick_count += 1

    def is_complete(self, current_time):
        return (current_time - self.timestamp).total_seconds() >= 5

    def __repr__(self):
        return f"Candle(O:{self.open} H:{self.high} L:{self.low} C:{self.close} V:{self.volume})"


class TickerManager:
    def __init__(self):
        self.kws = None
        self.tickers = {}
        self.app = create_app()
        self.k = None
        self.is_running = False
        self.current_candles = {}
        self.candle_history = defaultdict(lambda: deque(maxlen=20))
        self.data_lock = threading.Lock()
        self.candle_timer = None
        self.connected = False
        self.should_exit = False

    def is_market_open(self):
        """Check if market is currently open"""
        now = datetime.now(IST)

        # Skip weekends
        if now.weekday() >= 5:
            logger.info("Market closed: Weekend")
            return False

        # Check if within trading hours (9:10 AM to 3:30 PM IST)
        market_start = now.replace(hour=9, minute=10, second=0, microsecond=0)
        market_end = now.replace(hour=15, minute=30, second=0, microsecond=0)

        if now < market_start:
            logger.info(f"Market not yet open. Opens at {market_start}")
            return False
        elif now > market_end:
            logger.info(f"Market closed for the day. Closed at {market_end}")
            return False

        return True

    def initialize_connection(self):
        """Initialize KiteTicker connection with auto-login"""
        try:
            self.k = Kite()

            if not self.k.ensure_login():
                logger.error("Failed to login to Kite")
                with self.app.app_context():
                    admins = User.query.where(User.is_admin == True).all()
                    for admin in admins:
                        self.send_kite_login_alert(admin)
                return False

            logger.info("Successfully logged in to Kite")

            self.kws = KiteTicker(self.k.api_key, self.k.access_token)
            self.connected = False
            self.setup_handlers()
            self.start_candle_processor()
            return True

        except Exception as e:
            logger.error(f"Failed to initialize KiteTicker: {e}")
            return False

    def setup_handlers(self):
        self.kws.on_ticks = self.on_ticks
        self.kws.on_connect = self.on_connect
        self.kws.on_close = self.on_close
        self.kws.on_error = self.on_error

    def start_candle_processor(self):
        """Process completed candles every second"""
        if not self.should_exit:
            self.process_completed_candles()
            # Check if market is still open
            if not self.is_market_open():
                logger.info("Market closed during processing. Initiating shutdown...")
                self.should_exit = True
                return

            self.candle_timer = threading.Timer(1.0, self.start_candle_processor)
            self.candle_timer.start()

    def stop_candle_processor(self):
        if self.candle_timer:
            self.candle_timer.cancel()

    def load_tickers(self):
        try:
            with self.app.app_context():
                stmt = select(Ticker)
                tickers = db.session.execute(stmt).scalars().all()
                self.tickers = {ticker.instrument_token: ticker for ticker in tickers}
                return list(self.tickers.keys())
        except Exception as e:
            logger.error(f"Failed to load tickers: {e}")
            return []

    def is_trading_hours(self, tick_time):
        tick_time_ist = tick_time.astimezone(IST)
        if tick_time_ist.weekday() >= 5:
            return False
        market_start = tick_time_ist.replace(hour=9, minute=15, second=0, microsecond=0)
        market_end = tick_time_ist.replace(hour=15, minute=30, second=0, microsecond=0)
        return market_start <= tick_time_ist <= market_end

    def get_candle_timestamp(self, timestamp):
        # Always work in UTC for candle timestamps
        if timestamp.tzinfo is None:
            timestamp_utc = timestamp.replace(tzinfo=timezone.utc)
        else:
            timestamp_utc = timestamp.astimezone(timezone.utc)
        seconds = timestamp_utc.second
        floored_seconds = (seconds // 5) * 5
        return timestamp_utc.replace(second=floored_seconds, microsecond=0)

    def process_tick(self, instrument_token, price, volume=0, timestamp=None):
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        elif timestamp.tzinfo is None:
            # If timestamp has no timezone info, assume it's UTC
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        else:
            # Convert to UTC for consistency
            timestamp = timestamp.astimezone(timezone.utc)

        candle_timestamp = self.get_candle_timestamp(timestamp)

        with self.data_lock:
            if (instrument_token not in self.current_candles or
                    self.current_candles[instrument_token].timestamp != candle_timestamp):
                if instrument_token in self.current_candles:
                    prev_candle = self.current_candles[instrument_token]
                    self.candle_history[instrument_token].append(prev_candle)
                self.current_candles[instrument_token] = CandleData(candle_timestamp, price)

            self.current_candles[instrument_token].update_tick(price, volume)

    def process_completed_candles(self):
        current_time = datetime.now(timezone.utc)
        completed_instruments = []
        with self.data_lock:
            for instrument_token, candle in self.current_candles.items():
                if candle.is_complete(current_time):
                    completed_instruments.append((instrument_token, candle))

        for instrument_token, candle in completed_instruments:
            try:
                ticker = self.tickers[instrument_token]
                self.update_ticker_price(ticker, candle.close, current_time)
                self.check_trades(ticker.id, candle)
                with self.data_lock:
                    if instrument_token in self.current_candles:
                        self.candle_history[instrument_token].append(candle)
                        del self.current_candles[instrument_token]
            except Exception as e:
                logger.error(f"Error processing completed candle for {instrument_token}: {e}")

    def update_ticker_price(self, ticker, price, timestamp):
        try:
            with self.app.app_context():
                db_ticker = db.session.get(Ticker, ticker.id)
                if db_ticker:
                    db_ticker.last_price = price
                    db_ticker.last_updated = timestamp
                    db.session.commit()
        except Exception as e:
            logger.error(f"Failed to update ticker {ticker.symbol}: {e}")
            with self.app.app_context():
                db.session.rollback()

    def check_trades(self, ticker_id, candle: CandleData):
        try:
            with self.app.app_context():
                active_trades = Trade.get_active_trades_for_ticker(ticker_id)
                for trade in active_trades:
                    if trade.check(candle):
                        logger.info(f"Trade status changed: {trade} (Candle: {candle})")
                        self.send_trade_notification(trade.user, trade)
                    trade.update_etas()
        except Exception as e:
            logger.error(f"Error checking trades for ticker {ticker_id}: {e}")

    def send_kite_login_alert(self, user):
        """Send Kite login alert - implement as per your notification system"""
        logger.info(f"Sending Kite login alert to user {user.id}")
        # TODO: Implement actual notification sending logic
        pass

    def send_trade_notification(self, user, trade):
        """Send trade notification - implement as per your notification system"""
        logger.info(f"Sending trade notification to user {user.id} for trade {trade.id}")
        from app.services.telegram_service import telegram_service
        telegram_service.send_trade_alert(user, trade)
        pass

    def on_ticks(self, ws, ticks):
        for tick in ticks:
            try:
                if 'last_price' in tick and 'last_trade_time' in tick and tick['instrument_token'] in self.tickers:
                    last_trade_time = tick['last_trade_time']
                    if not self.is_trading_hours(last_trade_time):
                        continue
                    self.process_tick(tick['instrument_token'], tick['last_price'], tick.get('volume', 0),
                                      last_trade_time)
            except Exception as e:
                logger.error(f"Error processing tick: {e}")

    def on_connect(self, ws, response):
        logger.info("Successfully connected to WebSocket")
        self.connected = True
        instrument_tokens = self.load_tickers()
        if instrument_tokens:
            ws.subscribe(instrument_tokens)
            ws.set_mode(ws.MODE_FULL, instrument_tokens)
            logger.info(f"Subscribed to {len(instrument_tokens)} instruments")
        else:
            logger.warning("No instruments to subscribe to")

    def on_close(self, ws, code, reason):
        logger.warning(f"Connection closed: {code} - {reason}")
        self.should_exit = True

    def on_error(self, ws, code, reason):
        logger.error(f"Error in WebSocket: {code} - {reason}")
        self.should_exit = True

    def start(self):
        try:
            self.is_running = True
            self.kws.connect(threaded=True)
            logger.info("WebSocket connection started")
            return True
        except Exception as e:
            logger.error(f"Failed to start WebSocket: {e}")
            self.is_running = False
            return False

    def stop(self):
        try:
            self.is_running = False
            self.should_exit = True
            self.stop_candle_processor()
            if self.kws:
                self.kws.close()
            logger.info("WebSocket connection stopped")
        except Exception as e:
            logger.error(f"Error stopping WebSocket: {e}")

    def run_during_market_hours(self):
        """Run the ticker manager during market hours and exit when market closes"""

        # Check if market is open before starting
        if not self.is_market_open():
            logger.info("Market is not open. Exiting...")
            return False

        logger.info("Market is open. Starting ticker manager...")

        # Initialize connection
        if not self.initialize_connection():
            logger.error("Failed to initialize connection. Exiting...")
            return False

        # Start WebSocket connection
        if not self.start():
            logger.error("Failed to start WebSocket. Exiting...")
            return False

        # Wait for connection
        connection_timeout = 10  # seconds
        start_time = time.time()
        while not self.connected and (time.time() - start_time) < connection_timeout:
            time.sleep(0.5)

        if not self.connected:
            logger.error("Failed to connect within timeout. Exiting...")
            self.stop()
            return False

        logger.info("Successfully connected. Running during market hours...")

        # Monitor market hours and connection status
        try:
            while self.is_running and not self.should_exit:
                # Check market status every 30 seconds
                if not self.is_market_open():
                    logger.info("Market has closed. Stopping...")
                    break

                time.sleep(30)

        except KeyboardInterrupt:
            logger.info("Received interrupt signal. Stopping...")
        except Exception as e:
            logger.error(f"Unexpected error during runtime: {e}")
        finally:
            self.stop()
            logger.info("Ticker manager stopped. Exiting...")

        return True


def main():
    logger.info("Starting Stock Price Alert WebSocket Manager")

    manager = TickerManager()
    success = manager.run_during_market_hours()

    # Exit with appropriate code
    exit_code = 0 if success else 1
    logger.info(f"Script completed with exit code: {exit_code}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()