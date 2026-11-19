"""
Dataset Definitions (app/modules/company/datasets.py)
Data-driven configuration for structured business datasets.
"""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class DatasetDefinition:
    key: str
    label: str
    record_label: str
    collection_name: str
    natural_key: List[str]
    required_columns: List[str]
    optional_columns: List[str] = field(default_factory=list)
    field_types: Dict[str, str] = field(default_factory=dict)
    field_descriptions: Dict[str, str] = field(default_factory=dict)
    queryable_fields: Dict[str, bool] = field(default_factory=dict)

    @property
    def all_columns(self) -> List[str]:
        return list(self.required_columns) + [
            c for c in self.optional_columns if c not in self.required_columns
        ]

    def is_queryable(self, field_name: str) -> bool:
        return self.queryable_fields.get(field_name, True)


DATASET_CATALOG: Dict[str, DatasetDefinition] = {
    "customers": DatasetDefinition(
        key="customers",
        label="Customers",
        record_label="customer",
        collection_name="customers",
        natural_key=["customer_id"],
        required_columns=[
            "customer_id",
            "name",
            "email",
            "phone",
            "city",
            "region",
        ],
        optional_columns=["address", "postal_code", "status"],
        field_types={
            "customer_id": "string",
            "name": "string",
            "email": "email",
            "phone": "phone",
            "city": "string",
            "region": "string",
            "address": "string",
            "postal_code": "string",
            "status": "string",
        },
        field_descriptions={
            "customer_id": "Unique customer account ID",
            "name": "Customer company or contact name",
            "email": "Customer contact email address (personal data)",
            "phone": "Customer contact phone number (personal data)",
            "city": "City of business operations",
            "region": "Sales territory region (e.g. North, South, West, East)",
            "address": "Customer street address",
            "postal_code": "Postal code",
            "status": "Account status (e.g. active, inactive)",
        },
        queryable_fields={
            "customer_id": True,
            "name": True,
            "email": False,  # Personal data: not queryable
            "phone": False,  # Personal data: not queryable
            "city": True,
            "region": True,
            "address": True,
            "postal_code": True,
            "status": True,
        },
    ),
    "products": DatasetDefinition(
        key="products",
        label="Products",
        record_label="product",
        collection_name="products",
        natural_key=["product_id"],
        required_columns=[
            "product_id",
            "name",
            "sku",
            "category",
            "unit_price",
        ],
        optional_columns=["description", "reorder_level"],
        field_types={
            "product_id": "string",
            "name": "string",
            "sku": "string",
            "category": "string",
            "unit_price": "float",
            "description": "string",
            "reorder_level": "integer",
        },
        field_descriptions={
            "product_id": "Unique product SKU identifier",
            "name": "Commercial product name",
            "sku": "Stock keeping unit alphanumeric code",
            "category": "Product classification category",
            "unit_price": "Unit selling price in INR",
            "description": "Product specification details",
            "reorder_level": "Minimum inventory reorder trigger level",
        },
        queryable_fields={
            "product_id": True,
            "name": True,
            "sku": True,
            "category": True,
            "unit_price": True,
            "description": True,
            "reorder_level": True,
        },
    ),
    "orders": DatasetDefinition(
        key="orders",
        label="Orders",
        record_label="order",
        collection_name="orders",
        natural_key=["order_id"],
        required_columns=[
            "order_id",
            "order_date",
            "customer_name",
            "region",
            "status",
            "total",
        ],
        optional_columns=["items_count", "currency"],
        field_types={
            "order_id": "string",
            "order_date": "date",
            "customer_name": "string",
            "region": "string",
            "status": "string",
            "total": "float",
            "items_count": "integer",
            "currency": "string",
        },
        field_descriptions={
            "order_id": "Unique commercial order code",
            "order_date": "Order placement date and timestamp",
            "customer_name": "Purchasing customer or client name",
            "region": "Geographic sales region (e.g. North, South, West, East)",
            "status": "Order fulfillment status (completed, pending, cancelled)",
            "total": "Total order amount in INR",
            "items_count": "Total item count in order",
            "currency": "Order currency code",
        },
        queryable_fields={
            "order_id": True,
            "order_date": True,
            "customer_name": True,
            "region": True,
            "status": True,
            "total": True,
            "items_count": True,
            "currency": True,
        },
    ),
    "invoices": DatasetDefinition(
        key="invoices",
        label="Invoices",
        record_label="invoice",
        collection_name="invoices",
        natural_key=["invoice_number"],
        required_columns=[
            "invoice_number",
            "invoice_date",
            "due_date",
            "customer_name",
            "amount",
            "status",
        ],
        optional_columns=["paid_at", "currency"],
        field_types={
            "invoice_number": "string",
            "invoice_date": "date",
            "due_date": "date",
            "customer_name": "string",
            "amount": "float",
            "status": "string",
            "paid_at": "date",
            "currency": "string",
        },
        field_descriptions={
            "invoice_number": "Unique invoice reference number",
            "invoice_date": "Date of invoice issuance",
            "due_date": "Payment due date",
            "customer_name": "Invoiced client account name",
            "amount": "Total billing payable amount in INR",
            "status": "Invoice settlement status (paid, unpaid, overdue)",
            "paid_at": "Settlement payment timestamp",
            "currency": "Invoice currency",
        },
        queryable_fields={
            "invoice_number": True,
            "invoice_date": True,
            "due_date": True,
            "customer_name": True,
            "amount": True,
            "status": True,
            "paid_at": True,
            "currency": True,
        },
    ),
    "stock_levels": DatasetDefinition(
        key="stock_levels",
        label="Stock Levels",
        record_label="stock record",
        collection_name="stock_levels",
        natural_key=["product_name", "warehouse_location"],
        required_columns=[
            "product_name",
            "warehouse_location",
            "quantity_on_hand",
            "reorder_point",
        ],
        optional_columns=["unit", "last_counted_at"],
        field_types={
            "product_name": "string",
            "warehouse_location": "string",
            "quantity_on_hand": "integer",
            "reorder_point": "integer",
            "unit": "string",
            "last_counted_at": "date",
        },
        field_descriptions={
            "product_name": "Stocked item product name",
            "warehouse_location": "Warehouse facility location or hub name",
            "quantity_on_hand": "Units available in stock",
            "reorder_point": "Minimum inventory threshold level",
            "unit": "Unit of measurement",
            "last_counted_at": "Timestamp of physical stock count",
        },
        queryable_fields={
            "product_name": True,
            "warehouse_location": True,
            "quantity_on_hand": True,
            "reorder_point": True,
            "unit": True,
            "last_counted_at": True,
        },
    ),
}


def get_dataset_definition(dataset_key: str) -> DatasetDefinition | None:
    return DATASET_CATALOG.get(dataset_key.lower().strip())
