from marshmallow import Schema, fields, validate
from app.models.utils import TradeSide, TradeType, TradeTimeframe


class UserSchema(Schema):
    id = fields.Str(dump_only=True)
    name = fields.Str(required=True, validate=validate.Length(min=1, max=100))
    email = fields.Email(required=True)
    phone_number = fields.Str(validate=validate.Length(max=20))
    is_admin = fields.Bool(dump_only=True)
    created_at = fields.DateTime(dump_only=True)

    # Telegram fields
    telegram_enabled = fields.Bool(dump_only=True)
    telegram_username = fields.Str(dump_only=True)
    telegram_connected_at = fields.DateTime(dump_only=True)



class UserRegistrationSchema(Schema):
    name = fields.Str(required=True, validate=validate.Length(min=1, max=100))
    email = fields.Email(required=True)
    password = fields.Str(required=True, validate=validate.Length(min=6))
    phone_number = fields.Str(required=True, validate=validate.Length(max=20))


class UserLoginSchema(Schema):
    email = fields.Email(required=True)
    password = fields.Str(required=True)


class TickerSchema(Schema):
    id = fields.Str(dump_only=True)
    symbol = fields.Str(dump_only=True)
    exchange = fields.Str(dump_only=True)
    name = fields.Str(dump_only=True)
    last_price = fields.Float(dump_only=True)
    last_updated = fields.DateTime(dump_only=True)


# Base schema
class TagBaseSchema(Schema):
    name = fields.Str(required=True)


# Create schema (required)
class TagCreateSchema(TagBaseSchema):
    pass


# Update schema (optional)
class TagUpdateSchema(TagBaseSchema):
    pass


# Read schema
class TagReadSchema(TagBaseSchema):
    id = fields.Str(dump_only=True)


# Base schema with common fields
class TradeBaseSchema(Schema):
    notes = fields.Str()
    entry = fields.Float(validate=validate.Range(min=0))
    stoploss = fields.Float(validate=validate.Range(min=0))
    target = fields.Float(validate=validate.Range(min=0))
    timeframe = fields.Str(
        validate=validate.OneOf([TradeTimeframe.MINUTE, TradeTimeframe.FIVE_MINUTES, TradeTimeframe.FIFTEEN_MINUTES,
                                 TradeTimeframe.HOUR, TradeTimeframe.DAY, TradeTimeframe.WEEK, TradeTimeframe.MONTH]))
    score = fields.Int()
    entry_x = fields.DateTime()
    stoploss_x = fields.DateTime()
    target_x = fields.DateTime()


# CREATE schema (require essentials)
class TradeCreateSchema(TradeBaseSchema):
    ticker_id = fields.Str(required=True)  # or Int, depending on your FK type
    side = fields.Str(required=True, validate=validate.OneOf([TradeSide.BUY, TradeSide.SELL]))
    entry = fields.Float(required=True, validate=validate.Range(min=0))
    tags = fields.List(fields.Nested(TagCreateSchema))


# UPDATE schema (all optional — partial update)
class TradeUpdateSchema(TradeBaseSchema):
    type = fields.Str(validate=validate.OneOf([TradeType.CROSSING_ABOVE, TradeType.CROSSING_BELOW]))
    tags = fields.List(fields.Nested(TagUpdateSchema))


# READ schema (dump-only fields included)
class TradeReadSchema(TradeBaseSchema):
    id = fields.Str(dump_only=True)
    symbol = fields.Str(dump_only=True)
    last_price = fields.Float(dump_only=True)
    status = fields.Str(dump_only=True)
    side = fields.Str(dump_only=True)
    type = fields.Str(dump_only=True)
    entry_eta = fields.Str(dump_only=True)
    stoploss_eta = fields.Str(dump_only=True)
    target_eta = fields.Str(dump_only=True)
    entry_at = fields.DateTime(dump_only=True)
    stoploss_at = fields.DateTime(dump_only=True)
    target_at = fields.DateTime(dump_only=True)
    created_at = fields.DateTime(dump_only=True)
    edited_at = fields.DateTime(dump_only=True)
    updated_at = fields.DateTime(dump_only=True)
    status_updated_at = fields.DateTime(dump_only=True)
    ticker = fields.Nested(TickerSchema, dump_only=True)
    risk_reward_ratio = fields.Float(dump_only=True)
    risk_per_unit = fields.Float(dump_only=True)
    reward_per_unit = fields.Float(dump_only=True)
    tags = fields.List(fields.Nested(TagReadSchema))
