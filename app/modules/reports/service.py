"""
Reports Service (app/modules/reports/service.py)
Manages scheduled business reports, pause/resume state, and n8n background execution.
"""

from typing import Any, Dict, List
from app.core.email import get_email_sender
from app.core.exceptions import NotFoundError
from app.modules.chat.service import QueryService
from app.modules.reports.interfaces import ReportsRepoProtocol


class ReportService:
    def __init__(self, repo: ReportsRepoProtocol, query_service: QueryService):
        self.repo = repo
        self.query_service = query_service
        self.email_sender = get_email_sender()

    async def list_reports(self, company_id: str) -> List[Dict[str, Any]]:
        raw_reports = await self.repo.list_reports(company_id)
        result = []
        for r in raw_reports:
            result.append({
                "id": str(r["_id"]),
                "name": r["name"],
                "schedule": r["schedule"],
                "recipients": r.get("recipients", []),
                "status": r.get("status", "active"),
            })
        return result

    async def pause_report(self, company_id: str, report_id: str) -> Dict[str, str]:
        report = await self.repo.get_report_scoped(company_id, report_id)
        if not report:
            raise NotFoundError("Report not found.")
        await self.repo.update_report(report_id, {"status": "paused"})
        return {"id": report_id, "status": "paused"}

    async def resume_report(self, company_id: str, report_id: str) -> Dict[str, str]:
        report = await self.repo.get_report_scoped(company_id, report_id)
        if not report:
            raise NotFoundError("Report not found.")
        await self.repo.update_report(report_id, {"status": "active"})
        return {"id": report_id, "status": "active"}

    async def run_report(self, report_id: str) -> Dict[str, Any]:
        """Execute scheduled report under report tenant context and email recipients."""
        report = await self.repo.get_report_global(report_id)
        if not report:
            raise NotFoundError("Report not found.")

        cid = report["company_id"]
        query_text = report.get("query", "What is our total revenue?")
        recipients = report.get("recipients", [])
        recipient_ids = [r["id"] for r in recipients if "id" in r]
        emails = await self.repo.get_user_emails(recipient_ids)

        # Execute through safe validated QueryService under tenant context
        res = await self.query_service.answer(
            company_id=cid,
            user_id="system_report_runner",
            question=query_text,
            history=[],
        )

        headline = res.get("answer", {}).get("headline") or report["name"]
        summary = res.get("answer", {}).get("summary") or "Automated report delivery."
        text_body = f"Scheduled Report: {report['name']}\n\n{headline}\n{summary}\n"
        html_body = f"<h2>{report['name']}</h2><p><strong>{headline}</strong></p><p>{summary}</p>"

        for email in emails:
            await self.email_sender.send_email(
                recipient=email,
                subject=f"Scheduled Report: {report['name']}",
                text_body=text_body,
                html_body=html_body,
            )

        return {
            "report_id": report_id,
            "status": "completed",
            "recipients_emailed": len(emails),
        }
