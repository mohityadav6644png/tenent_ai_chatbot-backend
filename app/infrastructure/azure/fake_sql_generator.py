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
        conns = catalog.get("connections") or []
        if files:
            # Check for negative cross-file questions without connection
            if "teachers" in q and ("student" in q or "fail" in q or "english" in q):
                has_t_conn = any("teacher" in (c.get("alias") or "") for c in conns)
                if not has_t_conn:
                    return {
                        "decision": "refuse",
                        "refusal_reason": "no_connection",
                        "query": None,
                        "sql": None,
                        "clarification_question": None,
                        "options": [],
                        "headline_template": "These files aren't connected yet.",
                        "detail_template": "These files aren't connected yet. Ask your admin to connect them in Company data.",
                        "follow_ups": [],
                    }

            # Multi-part comparison query: e.g., "total orders and total customers" or "total marks and total students"
            if ("total" in q and "and total" in q) or ("orders and" in q and "customers" in q) or ("marks and" in q and "students" in q):
                file1 = next((f for f in files if "order" in f.get("slug", "") or "mark" in f.get("slug", "") or "exam" in f.get("slug", "")), None)
                file2 = next((f for f in files if "customer" in f.get("slug", "") or "student" in f.get("slug", "")), None)
                if file1 and file2:
                    return {
                        "decision": "answer",
                        "parts": [
                            {
                                "title": f"{file1.get('name', 'File 1')} Count",
                                "collection": file1["slug"],
                                "pipeline": [
                                    {"$count": "count"},
                                    {"$project": {"_id": 0, "count": 1}},
                                ],
                            },
                            {
                                "title": f"{file2.get('name', 'File 2')} Count",
                                "collection": file2["slug"],
                                "pipeline": [
                                    {"$count": "count"},
                                    {"$project": {"_id": 0, "count": 1}},
                                ],
                            },
                        ],
                        "headline": f"{file1.get('name', 'Data')} and {file2.get('name', 'Data')} Summary",
                        "detail": f"Counts from {file1.get('name', 'Data')} and {file2.get('name', 'Data')}.",
                        "follow_ups": [],
                    }

            # BrightPath: "What is the average marks by fee status?"
            if "fee status" in q and ("mark" in q or "average" in q):
                exam_f = next((f for f in files if "marks" in f.get("slug", "") or "exam" in f.get("slug", "")), None)
                st_conn = next((c for c in conns if c.get("alias") == "students" or "student" in c.get("alias", "")), None)
                if exam_f and st_conn:
                    alias = st_conn["alias"]
                    conn_id_val = str(st_conn.get("id") or st_conn.get("_id"))
                    q_spec = {
                        "collection": exam_f["slug"],
                        "joins": [conn_id_val],
                        "pipeline": [
                            {"$group": {"_id": f"${alias}.fee_status", "avg_marks": {"$avg": "$marks_obtained"}}},
                            {"$sort": {"avg_marks": -1}},
                            {"$project": {"_id": 0, "fee_status": "$_id", "avg_marks": {"$round": ["$avg_marks", 1]}}},
                        ],
                    }
                    return {
                        "decision": "answer",
                        "query": q_spec,
                        "sql": json.dumps(q_spec),
                        "headline_template": "Average marks by fee status: {row1.fee_status} is {row1.avg_marks}",
                        "detail_template": "Aggregated exam marks by student fee status.",
                        "follow_ups": [],
                    }
                else:
                    return {
                        "decision": "refuse",
                        "refusal_reason": "no_connection",
                        "headline_template": "These files aren't connected yet.",
                        "detail_template": "These files aren't connected yet. Ask your admin to connect them in Company data.",
                        "follow_ups": [],
                    }

            # BrightPath: "What is the average Maths marks by section?"
            if "maths" in q and "section" in q:
                exam_f = next((f for f in files if "marks" in f.get("slug", "") or "exam" in f.get("slug", "")), None)
                st_conn = next((c for c in conns if c.get("alias") == "students" or "student" in c.get("alias", "")), None)
                if exam_f and st_conn:
                    alias = st_conn["alias"]
                    conn_id_val = str(st_conn.get("id") or st_conn.get("_id"))
                    q_spec = {
                        "collection": exam_f["slug"],
                        "joins": [conn_id_val],
                        "pipeline": [
                            {"$match": {"subject": "Maths"}},
                            {"$group": {"_id": f"${alias}.section", "avg_marks": {"$avg": "$marks_obtained"}}},
                            {"$sort": {"avg_marks": -1}},
                            {"$project": {"_id": 0, "section": "$_id", "avg_marks": {"$round": ["$avg_marks", 1]}}},
                        ],
                    }
                    return {
                        "decision": "answer",
                        "query": q_spec,
                        "sql": json.dumps(q_spec),
                        "headline_template": "Average Maths marks by section: Section {row1.section} has {row1.avg_marks}",
                        "detail_template": "Maths performance across sections.",
                        "follow_ups": [],
                    }
                else:
                    return {
                        "decision": "refuse",
                        "refusal_reason": "no_connection",
                        "headline_template": "These files aren't connected yet.",
                        "detail_template": "These files aren't connected yet. Ask your admin to connect them in Company data.",
                        "follow_ups": [],
                    }

            # BrightPath: "Which students with Pending fee status failed at least one subject?"
            if "pending" in q and "fee status" in q and "fail" in q:
                exam_f = next((f for f in files if "marks" in f.get("slug", "") or "exam" in f.get("slug", "")), None)
                st_conn = next((c for c in conns if c.get("alias") == "students" or "student" in c.get("alias", "")), None)
                if exam_f and st_conn:
                    alias = st_conn["alias"]
                    conn_id_val = str(st_conn.get("id") or st_conn.get("_id"))
                    q_spec = {
                        "collection": exam_f["slug"],
                        "joins": [conn_id_val],
                        "pipeline": [
                            {"$match": {"marks_obtained": {"$lt": 40}, f"{alias}.fee_status": "Pending"}},
                            {"$group": {"_id": f"${alias}.name"}},
                            {"$sort": {"_id": 1}},
                            {"$project": {"_id": 0, "student_name": "$_id"}},
                        ],
                    }
                    return {
                        "decision": "answer",
                        "query": q_spec,
                        "sql": json.dumps(q_spec),
                        "headline_template": "Students with Pending fee status who failed a subject: {row1.student_name}",
                        "detail_template": "List of students with pending fees who scored below passing.",
                        "follow_ups": [],
                    }
                else:
                    return {
                        "decision": "refuse",
                        "refusal_reason": "no_connection",
                        "headline_template": "These files aren't connected yet.",
                        "detail_template": "These files aren't connected yet. Ask your admin to connect them in Company data.",
                        "follow_ups": [],
                    }

            # Acme Traders: "What is the total sales by customer segment / tier?"
            if "customer segment" in q or "tier" in q or ("segment" in q and "sales" in q):
                orders_f = next((f for f in files if "order" in f.get("slug", "")), None)
                cust_conn = next((c for c in conns if c.get("alias") == "customers" or "customer" in c.get("alias", "")), None)
                if orders_f and cust_conn:
                    alias = cust_conn["alias"]
                    cols = {c["key"] for c in orders_f.get("columns", [])}
                    metric = next((k for k in ["total", "amount", "sales", "price"] if k in cols), "total")
                    group_field = "tier" if "tier" in q else "segment"
                    conn_id_val = str(cust_conn.get("id") or cust_conn.get("_id"))
                    q_spec = {
                        "collection": orders_f["slug"],
                        "joins": [conn_id_val],
                        "pipeline": [
                            {"$group": {"_id": f"${alias}.{group_field}", "total_sales": {"$sum": f"${metric}"}}},
                            {"$sort": {"total_sales": -1}},
                            {"$project": {"_id": 0, group_field: "$_id", "total_sales": "$total_sales"}},
                        ],
                    }
                    return {
                        "decision": "answer",
                        "query": q_spec,
                        "sql": json.dumps(q_spec),
                        "headline_template": f"Total sales by customer {group_field}: {{row1.{group_field}}} leads with {{row1.total_sales}}",
                        "detail_template": f"Sales aggregated across customer {group_field}s.",
                        "follow_ups": [],
                    }
                else:
                    return {
                        "decision": "refuse",
                        "refusal_reason": "no_connection",
                        "headline_template": "These files aren't connected yet.",
                        "detail_template": "These files aren't connected yet. Ask your admin to connect them in Company data.",
                        "follow_ups": [],
                    }

            # Acme Traders / Products: unconnected negative query
            if "supplier" in q or ("product" in q and "customer" in q):
                return {
                    "decision": "refuse",
                    "refusal_reason": "no_connection",
                    "headline_template": "These files aren't connected yet.",
                    "detail_template": "These files aren't connected yet. Ask your admin to connect them in Company data.",
                    "follow_ups": [],
                }

            # Acme Traders: "Show the top 3 cities by sales"
            if "top 3 cities" in q or ("cities by sales" in q) or ("city" in q and "top" in q):
                orders_f = next((f for f in files if "order" in f.get("slug", "")), None)
                cust_conn = next((c for c in conns if c.get("alias") == "customers" or "customer" in c.get("alias", "")), None)
                if orders_f and cust_conn:
                    alias = cust_conn["alias"]
                    cols = {c["key"] for c in orders_f.get("columns", [])}
                    metric = next((k for k in ["total", "amount", "sales", "price"] if k in cols), "total")
                    conn_id_val = str(cust_conn.get("id") or cust_conn.get("_id"))
                    q_spec = {
                        "collection": orders_f["slug"],
                        "joins": [conn_id_val],
                        "pipeline": [
                            {"$group": {"_id": f"${alias}.city", "total_sales": {"$sum": f"${metric}"}}},
                            {"$sort": {"total_sales": -1}},
                            {"$limit": 3},
                            {"$project": {"_id": 0, "city": "$_id", "total_sales": "$total_sales"}},
                        ],
                    }
                    return {
                        "decision": "answer",
                        "query": q_spec,
                        "sql": json.dumps(q_spec),
                        "headline_template": "Top cities by sales: {row1.city} ({row1.total_sales}), {row2.city} ({row2.total_sales})",
                        "detail_template": "Top 3 performing cities by sales revenue.",
                        "follow_ups": [],
                    }
                else:
                    return {
                        "decision": "refuse",
                        "refusal_reason": "no_connection",
                        "headline_template": "These files aren't connected yet.",
                        "detail_template": "These files aren't connected yet. Ask your admin to connect them in Company data.",
                        "follow_ups": [],
                    }

            # Zenith Sales: "Show won deal value by manager or region"
            if "won deal value" in q or ("manager" in q and ("deal" in q or "won" in q)) or ("region" in q and ("deal" in q or "sales" in q)):
                deals_f = next((f for f in files if "deal" in f.get("slug", "")), None)
                sales_conn = next((c for c in conns if "sales" in c.get("alias", "") or "team" in c.get("alias", "")), None)
                if deals_f and sales_conn:
                    alias = sales_conn["alias"]
                    cols = {c["key"] for c in deals_f.get("columns", [])}
                    metric = next((k for k in ["value", "amount", "deal_value"] if k in cols), "value")
                    stage_k = next((k for k in ["stage", "status"] if k in cols), "stage")
                    group_field = "region" if "region" in q else "manager"
                    conn_id_val = str(sales_conn.get("id") or sales_conn.get("_id"))
                    q_spec = {
                        "collection": deals_f["slug"],
                        "joins": [conn_id_val],
                        "pipeline": [
                            {"$match": {stage_k: {"$in": ["Closed Won", "Won", "won"]}}},
                            {"$group": {"_id": f"${alias}.{group_field}", "won_deal_value": {"$sum": f"${metric}"}}},
                            {"$sort": {"won_deal_value": -1}},
                            {"$project": {"_id": 0, group_field: "$_id", "won_deal_value": "$won_deal_value"}},
                        ],
                    }
                    return {
                        "decision": "answer",
                        "query": q_spec,
                        "sql": json.dumps(q_spec),
                        "headline_template": f"Won deal value by {group_field}: {{row1.{group_field}}} leads with {{row1.won_deal_value}}",
                        "detail_template": f"Total won deal value broken down by sales {group_field}.",
                        "follow_ups": [],
                    }
                else:
                    return {
                        "decision": "refuse",
                        "refusal_reason": "no_connection",
                        "headline_template": "These files aren't connected yet.",
                        "detail_template": "These files aren't connected yet. Ask your admin to connect them in Company data.",
                        "follow_ups": [],
                    }

            # Zenith Sales: unconnected negative query
            if "target" in q or "quarterly" in q:
                return {
                    "decision": "refuse",
                    "refusal_reason": "no_connection",
                    "headline_template": "These files aren't connected yet.",
                    "detail_template": "These files aren't connected yet. Ask your admin to connect them in Company data.",
                    "follow_ups": [],
                }

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

                # BrightPath & Educational data aggregations
                if "marks_obtained" in cols and ("mark" in q or "subject" in q or "exam" in q or "lowest" in q or "highest" in q):
                    if "lowest" in q:
                        p = []
                        if "class 10" in q:
                            p.append({"$match": {"class": "Class 10"}})
                        p.extend([
                            {"$group": {"_id": "$subject", "avg_marks": {"$avg": "$marks_obtained"}}},
                            {"$sort": {"avg_marks": 1}},
                            {"$limit": 1},
                            {"$project": {"_id": 0, "subject": "$_id", "avg_marks": {"$round": ["$avg_marks", 1]}}},
                        ])
                        q_spec = {"collection": slug, "pipeline": p}
                        return {
                            "decision": "answer",
                            "query": q_spec,
                            "sql": json.dumps(q_spec),
                            "clarification_question": None,
                            "options": [],
                            "headline_template": "Lowest subject: {row1.subject} with {row1.avg_marks} average marks",
                            "detail_template": "Subject with lowest average marks in Class 10.",
                            "follow_ups": [],
                        }
                    elif "class 10" in q:
                        q_spec = {
                            "collection": slug,
                            "pipeline": [
                                {"$match": {"class": "Class 10"}},
                                {"$group": {"_id": "$subject", "avg_marks": {"$avg": "$marks_obtained"}}},
                                {"$sort": {"avg_marks": -1}},
                                {"$project": {"_id": 0, "subject": "$_id", "avg_marks": {"$round": ["$avg_marks", 1]}}},
                            ],
                        }
                        return {
                            "decision": "answer",
                            "query": q_spec,
                            "sql": json.dumps(q_spec),
                            "clarification_question": None,
                            "options": [],
                            "headline_template": "Class 10 average marks: Maths {row1.avg_marks}, English {row2.avg_marks}, Science {row3.avg_marks}",
                            "detail_template": "Subject-wise average marks for Class 10.",
                            "follow_ups": ["Which subject is the lowest?"],
                        }
                    else:
                        q_spec = {
                            "collection": slug,
                            "pipeline": [
                                {"$group": {"_id": "$subject", "avg_marks": {"$avg": "$marks_obtained"}}},
                                {"$sort": {"avg_marks": -1}},
                                {"$project": {"_id": 0, "subject": "$_id", "avg_marks": {"$round": ["$avg_marks", 1]}}},
                            ],
                        }
                        return {
                            "decision": "answer",
                            "query": q_spec,
                            "sql": json.dumps(q_spec),
                            "clarification_question": None,
                            "options": [],
                            "headline_template": "Average marks in each subject: {row1.subject} has {row1.avg_marks}",
                            "detail_template": "Overall subject averages across all classes.",
                            "follow_ups": ["What about only Class 10?"],
                        }

                if "class" in cols and ("student" in q or "class" in q):
                    if "section" in q and "section" in cols:
                        p = []
                        if "class 8" in q:
                            p.append({"$match": {"class": "Class 8"}})
                        elif "class 10" in q:
                            p.append({"$match": {"class": "Class 10"}})
                        p.extend([
                            {"$group": {"_id": {"class": "$class", "section": "$section"}, "count": {"$sum": 1}}},
                            {"$sort": {"_id.section": 1}},
                            {"$project": {"_id": 0, "class": "$_id.class", "section": "$_id.section", "count": 1}},
                        ])
                        q_spec = {"collection": slug, "pipeline": p}
                        return {
                            "decision": "answer",
                            "query": q_spec,
                            "sql": json.dumps(q_spec),
                            "clarification_question": None,
                            "options": [],
                            "headline_template": "Class 8 by section: A has {row1.count}, B has {row2.count}",
                            "detail_template": "Student distribution by class and section.",
                            "follow_ups": [],
                        }
                    elif "class 8" in q:
                        q_spec = {
                            "collection": slug,
                            "pipeline": [
                                {"$match": {"class": "Class 8"}},
                                {"$group": {"_id": "$class", "count": {"$sum": 1}}},
                                {"$project": {"_id": 0, "class": "$_id", "count": 1}},
                            ],
                        }
                        return {
                            "decision": "answer",
                            "query": q_spec,
                            "sql": json.dumps(q_spec),
                            "clarification_question": None,
                            "options": [],
                            "headline_template": "Class 8: {row1.count} students",
                            "detail_template": "Total students in Class 8.",
                            "follow_ups": ["And by section?"],
                        }
                    elif "each class" in q or "by class" in q or "class" in q:
                        q_spec = {
                            "collection": slug,
                            "pipeline": [
                                {"$group": {"_id": "$class", "count": {"$sum": 1}}},
                                {"$sort": {"_id": 1}},
                                {"$project": {"_id": 0, "class": "$_id", "count": 1}},
                            ],
                        }
                        return {
                            "decision": "answer",
                            "query": q_spec,
                            "sql": json.dumps(q_spec),
                            "clarification_question": None,
                            "options": [],
                            "headline_template": "Students in each class: {row1.class} has {row1.count}",
                            "detail_template": "Aggregated student counts across classes.",
                            "follow_ups": ["Only for Class 8"],
                        }

                if "fee_status" in cols and ("fee" in q or "status" in q or "pending" in q):
                    if "pending" in q:
                        q_spec = {
                            "collection": slug,
                            "pipeline": [
                                {"$match": {"fee_status": "Pending"}},
                                {"$group": {"_id": "$fee_status", "count": {"$sum": 1}}},
                                {"$project": {"_id": 0, "fee_status": "$_id", "count": 1}},
                            ],
                        }
                        return {
                            "decision": "answer",
                            "query": q_spec,
                            "sql": json.dumps(q_spec),
                            "clarification_question": None,
                            "options": [],
                            "headline_template": "Pending fee status: {row1.count} students",
                            "detail_template": "Count of students with pending fees.",
                            "follow_ups": [],
                        }
                    else:
                        q_spec = {
                            "collection": slug,
                            "pipeline": [
                                {"$group": {"_id": "$fee_status", "count": {"$sum": 1}}},
                                {"$sort": {"count": -1}},
                                {"$project": {"_id": 0, "fee_status": "$_id", "count": 1}},
                            ],
                        }
                        return {
                            "decision": "answer",
                            "query": q_spec,
                            "sql": json.dumps(q_spec),
                            "clarification_question": None,
                            "options": [],
                            "headline_template": "Fee status distribution: {row1.fee_status} has {row1.count}",
                            "detail_template": "Students grouped by fee status.",
                            "follow_ups": ["Show only Pending"],
                        }

                if "teacher" in q and "subject" in cols:
                    q_spec = {
                        "collection": slug,
                        "pipeline": [
                            {"$group": {"_id": "$subject", "count": {"$sum": 1}}},
                            {"$sort": {"count": -1}},
                            {"$project": {"_id": 0, "subject": "$_id", "count": 1}},
                        ],
                    }
                    return {
                        "decision": "answer",
                        "query": q_spec,
                        "sql": json.dumps(q_spec),
                        "clarification_question": None,
                        "options": [],
                        "headline_template": "Teachers per subject: {row1.subject} has {row1.count}",
                        "detail_template": "Count of teachers allocated to each subject.",
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

