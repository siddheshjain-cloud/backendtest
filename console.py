from app import create_app
from app import db
from app.models import *

app = create_app(register_research=True)
app.app_context().push()

users = User.query.all()
admins = User.query.where(User.is_admin==True).all()