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

        # Company files integration
        files = catalog.get("files") or []
        if files:
            # Check for hidden column requests (e.g., phone numbers when phone is hidden)
            non_hidden_keys = {c.get("key") for f in files for c in f.get("columns", [])}
            if "phone" in q and "phone" not in non_hidden_keys:
                return {
                    "decision": "refuse",
                    "query": None,
                    "sql": None,
                    "clarification_question": None,
                    "options": [],
                    "headline_template": "Request Refused",
                    "detail_template": "Phone column is private and cannot be read by the chatbot.",
                    "follow_ups": [],
                }

            # Check if several files fit equally or need clarification
            if q in ["status", "overview", "show data", "tell me about files"]:
                return {
                    "decision": "clarify",
                    "query": None,
                    "sql": None,
                    "clarification_question": "Which company file would you like to inspect?",
                    "options": [f.get("name") for f in files[:4]],
                    "headline_template": None,
                    "detail_template": None,
                    "follow_ups": [],
                }

            # Score matching files
            matched_files = []
            for f in files:
                f_name = f.get("name", "").lower()
                f_desc = f.get("description", "").lower()
                f_slug = f.get("slug", "").lower()
                cols = {c.get("key", "").lower() for c in f.get("columns", [])}
                score = 0
                if any(w in q for w in f_name.split()):
                    score += 3
                if any(w in q for w in f_slug.split("_") if len(w) > 2):
                    score += 2
                if any(c in q for c in cols):
                    score += 2
                if any(w in q for w in f_desc.split() if len(w) > 3):
                    score += 1
                if score > 0:
                    matched_files.append((score, f))

            if len(matched_files) > 1 and matched_files[0][0] == matched_files[1][0] and matched_files[0][0] > 0:
                return {
                    "decision": "clarify",
                    "query": None,
                    "sql": None,
                    "clarification_question": "Multiple files match your request. Please select one:",
                    "options": [mf[1].get("name") for mf in matched_files[:4]],
                    "headline_template": None,
                    "detail_template": None,
                    "follow_ups": [],
                }

            if matched_files:
                matched_files.sort(key=lambda x: x[0], reverse=True)
                target_file = matched_files[0][1]
                slug = target_file["slug"]
                cols = {c["key"]: c for c in target_file.get("columns", [])}

                # Match queries like "total sales by region" or "sales by region"
                if "region" in q and "region" in cols:
                    metric = next((k for k in ["total", "sales", "amount", "revenue", "price"] if k in cols), None)
                    if metric:
                        q_spec = {
                            "collection": slug,
                            "pipeline": [
                                {"$group": {"_id": "$region", "total_sales": {"$sum": f"${metric}"}}},
                                {"$sort": {"total_sales": -1}},
                                {"$limit": 100},
                                {"$project": {"_id": 0, "region": "$_id", "total_sales": 1}},
                            ],
                        }
                    else:
                        q_spec = {
                            "collection": slug,
                            "pipeline": [
                                {"$group": {"_id": "$region", "count": {"$sum": 1}}},
                                {"$sort": {"count": -1}},
                                {"$limit": 100},
                                {"$project": {"_id": 0, "region": "$_id", "count": 1}},
                            ],
                        }
                    return {
                        "decision": "answer",
                        "query": q_spec,
                        "sql": json.dumps(q_spec),
                        "clarification_question": None,
                        "options": [],
                        "headline_template": "Total sales by region: {row1.region} has {row1.total_sales}",
                        "detail_template": "Aggregated sales data across regions.",
                        "follow_ups": [],
                    }

                # General row list / count on target file
                first_cols = list(cols.keys())[:3]
                proj = {"_id": 0}
                for c in first_cols:
                    proj[c] = 1
                q_spec = {
                    "collection": slug,
                    "pipeline": [
                        {"$limit": 10},
                        {"$project": proj},
                    ],
                }
                return {
                    "decision": "answer",
                    "query": q_spec,
                    "sql": json.dumps(q_spec),
                    "clarification_question": None,
                    "options": [],
                    "headline_template": f"Data from {target_file.get('name')}",
                    "detail_template": f"Displaying {len(first_cols)} columns from file.",
                    "follow_ups": [],
                }

            # If files exist but none matches the question:
            if not any(k in q for k in ["customer", "order", "product", "stock", "invoice"]):
                return {
                    "decision": "refuse",
                    "query": None,
                    "sql": None,
                    "clarification_question": None,
                    "options": [],
                    "headline_template": "Request Refused",
                    "detail_template": "None of your company files contain data relevant to this question.",
                    "follow_ups": [],
                }

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

        if "region" in q and "customer" in q:
            q_spec = {
                "collection": "customers",
                "pipeline": [
                    {"$group": {"_id": "$region", "count": {"$sum": 1}}},
                    {"$sort": {"count": -1}},
                    {"$project": {"_id": 0, "region": "$_id", "count": 1}},
                ],
            }
            return {
                "decision": "answer",
                "query": q_spec,
                "sql": json.dumps(q_spec),
                "clarification_question": None,
                "options": [],
                "headline_template": "Customer count by region: {row1.region} has {row1.count}",
                "detail_template": "Aggregated customers across regions.",
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

