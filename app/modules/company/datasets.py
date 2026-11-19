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

    @property
    def all_columns(self) -> List[str]:
        return list(self.required_columns) + [
            c for c in self.optional_columns if c not in self.required_columns
        ]


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
    ),
}


def get_dataset_definition(dataset_key: str) -> DatasetDefinition | None:
    return DATASET_CATALOG.get(dataset_key.lower().strip())
