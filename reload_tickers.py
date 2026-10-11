from kite import Kite
from app import create_app, db
from app.models import Ticker
import pandas as pd

df = pd.read_csv('stocks.csv')

kite = Kite().kite
instruments = kite.instruments("NSE")
instruments = [i for i in instruments if i['tradingsymbol'] in df['SYMBOL'].values]

app = create_app(allow_default_database=True)

with app.app_context():
    for i in instruments:
        ticker = Ticker(
            symbol=i['tradingsymbol'],
            exchange=i['exchange'],
            instrument_token=i['instrument_token'],
            name=i['name'],
        )
        db.session.add(ticker)

    db.session.commit()
