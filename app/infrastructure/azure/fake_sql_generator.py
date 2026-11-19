"""
Fake SQL Generator (app/infrastructure/azure/fake_sql_generator.py)
SQLGenerator now generates a MongoDB query spec.
Deterministic offline generator for MongoDB aggregation specs, refusals, and clarifications.
"""

import json
from typing import Any, Dict, List


class FakeSQLGenerator:
    """Mock generator returning deterministic MongoDB aggregation specs without Azure."""

    async def generate(
        self, question: str, catalog: Dict[str, Any], history: List[Dict[str, str]], company_id: str
    ) -> Dict[str, Any]:
        q = question.strip().lower()

        # Adversarial and off-topic refusals
        refusal_keywords = [
            "ignore previous", "system prompt", "drop table", "show all companies", "company_id",
            "poem", "joke", "weather", "dan mode", "sql injection", "reveal prompt",
            "bypass", "disregard", "override", "capital of", "who wrote", "recipe",
            "president", "meaning of life", "sing a song", "sports score",
        ]
        if any(bad in q for bad in refusal_keywords):
            return {"decision": "refuse", "query": None, "sql": None, "clarification_question": None, "options": [], "headline_template": None, "detail_template": None, "follow_ups": []}

        # Clarification prompts
        if q in ["best products", "how are we doing", "status", "tell me more", "show details"]:
            return {
                "decision": "clarify",
                "query": None,
                "sql": None,
                "clarification_question": "Would you like to review sales revenue, order counts, or inventory levels?",
                "options": ["Sales revenue", "Order counts", "Inventory levels"],
                "headline_template": None,
                "detail_template": None,
                "follow_ups": [],
            }

        if "customer" in q:
            q_spec = {
                "collection": "orders",
                "pipeline": [
                    {"$match": {"status": "completed"}},
                    {"$group": {"_id": "$customer_name", "total_spent": {"$sum": "$total"}}},
                    {"$sort": {"total_spent": -1}},
                    {"$limit": 5},
                    {"$project": {"_id": 0, "name": "$_id", "total_spent": 1}},
                ],
            }
            return {
                "decision": "answer",
                "query": q_spec,
                "sql": json.dumps(q_spec),
                "clarification_question": None,
                "options": [],
                "headline_template": "Top Customer: {row1.name} (₹{row1.total_spent})",
                "detail_template": "Ranked top {row_count} customer accounts by total revenue.",
                "follow_ups": ["Show orders for {row1.name}", "Show customer cities"],
            }

        if "stock" in q or "inventory" in q:
            q_spec = {
                "collection": "stock_levels",
                "pipeline": [
                    {"$match": {"quantity_on_hand": {"$lte": 20}}},
                    {"$sort": {"quantity_on_hand": 1}},
                    {"$limit": 100},
                    {"$project": {"_id": 0, "product_name": 1, "warehouse_location": 1, "quantity_on_hand": 1, "reorder_threshold": 1}},
                ],
            }
            return {
                "decision": "answer",
                "query": q_spec,
                "sql": json.dumps(q_spec),
                "clarification_question": None,
                "options": [],
                "headline_template": "Found {row_count} items at or below reorder threshold",
                "detail_template": "Lowest stock: {row1.product_name} with {row1.quantity_on_hand} units in {row1.warehouse_location}.",
                "follow_ups": ["Show products in stock", "Show warehouse depots"],
            }

        if "invoice" in q:
            q_spec = {
                "collection": "invoices",
                "pipeline": [
                    {"$match": {"status": "overdue"}},
                    {"$sort": {"invoice_date": 1}},
                    {"$limit": 100},
                    {"$project": {"_id": 0, "invoice_number": 1, "invoice_date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$invoice_date"}}, "amount": 1, "status": 1}},
                ],
            }
            return {
                "decision": "answer",
                "query": q_spec,
                "sql": json.dumps(q_spec),
                "clarification_question": None,
                "options": [],
                "headline_template": "{row_count} overdue invoices identified",
                "detail_template": "First overdue invoice: {row1.invoice_number} for ₹{row1.amount}.",
                "follow_ups": ["Show all unpaid invoices"],
            }

        # Default completed revenue aggregation
        q_spec = {
            "collection": "orders",
            "pipeline": [
                {"$match": {"status": "completed"}},
                {"$group": {"_id": None, "total_revenue": {"$sum": "$total"}}},
                {"$limit": 1},
                {"$project": {"_id": 0, "total_revenue": 1}},
            ],
        }
        return {
            "decision": "answer",
            "query": q_spec,
            "sql": json.dumps(q_spec),
            "clarification_question": None,
            "options": [],
            "headline_template": "Total Revenue: ₹{row1.total_revenue}",
            "detail_template": "Aggregated across completed orders.",
            "follow_ups": ["Show top 5 customers", "Show product categories"],
        }

