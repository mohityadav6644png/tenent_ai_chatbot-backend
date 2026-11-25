"""
Fake Follow-up Resolver (app/infrastructure/azure/fake_followup.py)
Deterministic offline follow-up resolver for testing and fake provider mode.
Handles filter additions, groupings, positional references, and topic switches.
"""

import re
from typing import Any, Dict, List, Optional
from app.modules.chat.interfaces import FollowUpResolver


class FakeFollowUpResolver:
    """Deterministic follow-up resolver implementing FollowUpResolver protocol."""

    async def resolve(
        self,
        new_message: str,
        turns: List[Dict[str, Any]],
        catalog: Dict[str, Any],
        company_id: str,
    ) -> Dict[str, Any]:
        msg = new_message.strip()
        msg_lower = msg.lower()

        # 1. Positional references that cannot be resolved without row data -> needs_clarification
        positional_patterns = [
            r"\b(the\s+)?(first|second|third|fourth|fifth|last)\s+(one|record|row|customer|item|student|teacher)\b",
            r"\bshow\s+(the\s+)?(first|second|third|fourth|fifth|last)\s+one\b",
            r"\bthat\s+(customer|student|teacher|record|item|one)\b",
        ]
        if any(re.search(pat, msg_lower) for pat in positional_patterns):
            # Derive target entity from previous turn
            last_q = turns[-1].get("resolved_question", "") if turns else ""
            entity = "item"
            if "class" in last_q.lower():
                entity = "class"
            elif "student" in last_q.lower():
                entity = "student"
            elif "customer" in last_q.lower():
                entity = "customer"
            return {
                "is_followup": True,
                "standalone_question": msg,
                "needs_clarification": True,
                "clarification_question": f"Which {entity} would you like to see? Please specify the name.",
            }

        if not turns:
            return {
                "is_followup": False,
                "standalone_question": msg,
                "needs_clarification": False,
                "clarification_question": None,
            }

        prev_turn = turns[-1]
        prev_q = prev_turn.get("resolved_question") or prev_turn.get("question") or ""
        prev_slug = prev_turn.get("file_slug")

        # 2. Topic switches: question is independent if it explicitly asks about a different file/domain
        teachers_match = "teacher" in msg_lower
        marks_prev = "mark" in prev_q.lower() or "student" in prev_q.lower()
        if teachers_match and marks_prev and "student" not in msg_lower:
            return {
                "is_followup": False,
                "standalone_question": msg,
                "needs_clarification": False,
                "clarification_question": None,
            }

        # 3. Last turn was clarification
        if prev_turn.get("last_was_clarification") or prev_turn.get("status") == "clarification_needed":
            opts = prev_turn.get("clarification_options") or []
            matched_opt = next((opt for opt in opts if opt.lower() in msg_lower or msg_lower in opt.lower()), None)
            standalone = matched_opt if matched_opt else f"{prev_q} - {msg}"
            return {
                "is_followup": True,
                "standalone_question": standalone,
                "needs_clarification": False,
                "clarification_question": None,
            }

        # 4. Standard follow-up rewrites
        # "Only for Class 8"
        if re.search(r"\bonly\s+(for\s+)?class\s+(\d+|[a-zA-Z0-9]+)\b", msg_lower):
            class_match = re.search(r"class\s+(\d+|[a-zA-Z0-9]+)", msg, re.IGNORECASE)
            class_val = class_match.group(0) if class_match else "Class 8"
            standalone = f"How many students are in {class_val}?"
            return {
                "is_followup": True,
                "standalone_question": standalone,
                "needs_clarification": False,
                "clarification_question": None,
            }

        # "And by section?"
        if "section" in msg_lower and "by" in msg_lower:
            # Check if previous was class 8
            if "class 8" in prev_q.lower():
                standalone = "How many students are in Class 8 by section?"
            else:
                standalone = f"{prev_q} by section"
            return {
                "is_followup": True,
                "standalone_question": standalone,
                "needs_clarification": False,
                "clarification_question": None,
            }

        # "What about only Class 10?"
        if "class 10" in msg_lower:
            standalone = "What is the average marks in each subject for Class 10?"
            return {
                "is_followup": True,
                "standalone_question": standalone,
                "needs_clarification": False,
                "clarification_question": None,
            }

        # "Which subject is the lowest?" / "Which one is lowest?"
        if "lowest" in msg_lower or "highest" in msg_lower:
            metric = "lowest" if "lowest" in msg_lower else "highest"
            if "class 10" in prev_q.lower() or "marks" in prev_q.lower():
                standalone = f"Which subject is the {metric} in average marks for Class 10?"
            else:
                standalone = f"{prev_q} - which is {metric}?"
            return {
                "is_followup": True,
                "standalone_question": standalone,
                "needs_clarification": False,
                "clarification_question": None,
            }

        # "Show only Pending" / "Only Pending"
        if "pending" in msg_lower and ("fee" in prev_q.lower() or "status" in prev_q.lower()):
            standalone = "How many students have fee status Pending?"
            return {
                "is_followup": True,
                "standalone_question": standalone,
                "needs_clarification": False,
                "clarification_question": None,
            }

        # Heuristic merge: if message starts with "only", "and", "by", "for", "what about"
        followup_starters = ["only", "and", "by", "for", "what about", "how about", "in", "show only", "where"]
        if any(msg_lower.startswith(s) for s in followup_starters):
            cleaned_prev = prev_q.rstrip("?.")
            clean_msg = re.sub(r"^(only|and|what about|how about)\s+", "", msg, flags=re.IGNORECASE)
            standalone = f"{cleaned_prev} for {clean_msg}"
            return {
                "is_followup": True,
                "standalone_question": standalone,
                "needs_clarification": False,
                "clarification_question": None,
            }

        # Default fallback: independent
        return {
            "is_followup": False,
            "standalone_question": msg,
            "needs_clarification": False,
            "clarification_question": None,
        }
