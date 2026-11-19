"""
SQL Guard Rules (app/modules/sql_guard/rules.py)
modules/sql_guard now validates MongoDB aggregation pipelines.
Defines allow-lists, forbidden operators, stage limits, and collection field schemas.
"""

# Allowed pipeline stage names
ALLOWED_STAGES = frozenset({
    "$match", "$group", "$project", "$sort", "$limit",
    "$skip", "$unwind", "$count", "$addFields", "$set",
})

# Explicitly forbidden operators anywhere in query tree
FORBIDDEN_OPERATORS = frozenset({
    "$out", "$merge", "$lookup", "$graphLookup", "$unionWith", "$facet",
    "$function", "$accumulator", "$where", "$getField", "$setWindowFields",
    "$search", "$vectorSearch", "$currentOp", "$listSessions", "$collStats",
    "$indexStats", "$planCacheStats",
})

# Allowed expression and query operators
ALLOWED_OPERATORS = frozenset({
    "$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in", "$nin", "$and", "$or", "$nor", "$not",
    "$exists", "$sum", "$avg", "$min", "$max", "$first", "$last", "$push", "$addToSet", "$count",
    "$add", "$subtract", "$multiply", "$divide", "$mod", "$abs", "$round", "$ceil", "$floor",
    "$cond", "$ifNull", "$switch", "$dateTrunc", "$dateToString", "$year", "$month", "$dayOfMonth",
    "$dayOfWeek", "$toLower", "$toUpper", "$concat", "$substr", "$size", "$arrayElemAt", "$toInt", "$toDouble",
    "$regex", "$regexMatch", "$date",
}) | ALLOWED_STAGES

# Business collections catalog fields (only queryable fields allowed)
COLLECTION_CATALOG_FIELDS = {
    "orders": frozenset({
        "_id", "id", "order_id", "customer_name", "region", "status", "order_date", "total",
        "items", "items.product_name", "items.category", "items.qty", "items.price",
        "items_count", "currency",
    }),
    "customers": frozenset({
        "_id", "id", "customer_id", "name", "city", "region", "address", "postal_code", "status", "created_at",
    }),
    "products": frozenset({
        "_id", "id", "product_id", "name", "sku", "category", "unit_price", "price", "description", "reorder_level",
    }),
    "invoices": frozenset({
        "_id", "id", "invoice_number", "invoice_date", "due_date", "customer_name", "amount", "status", "paid_at", "currency",
    }),
    "stock_levels": frozenset({
        "_id", "id", "product_name", "warehouse_location", "quantity_on_hand", "reorder_threshold", "reorder_point", "unit", "last_counted_at",
    }),
}

DEFAULT_ALLOWED_COLLECTIONS = frozenset(COLLECTION_CATALOG_FIELDS.keys())

# Pipeline complexity limits
MAX_STAGES = 8
MAX_BYTES = 8192  # 8 KB
MAX_DEPTH = 6
MAX_ROWS = 100
MAX_REGEX_LEN = 40
