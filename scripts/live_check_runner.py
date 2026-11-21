import asyncio
import io
import httpx
from app.main import app

async def run_live_check():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Log in as asha@acme.in
        login_res = await client.post("/v1/auth/login", json={
            "email": "asha@acme.in",
            "password": "Demo@1234",
        })
        if login_res.status_code != 200:
            print(f"Login failed: {login_res.status_code} {login_res.text}")
            return

        login_data = login_res.json()
        csrf_token = login_res.cookies.get("csrf_token")
        cookies = dict(login_res.cookies)

        headers = {
            "X-CSRF-Token": csrf_token,
        }

        # 2. Import tiny fake customers CSV with 3 rows
        csv_content = (
            "customer_id,name,city,region,email,phone\n"
            "CUST-LIVE-1,Live Customer 1,Mumbai,West,live1@acme.in,+919999900001\n"
            "CUST-LIVE-2,Live Customer 2,Delhi,North,live2@acme.in,+919999900002\n"
            "CUST-LIVE-3,Live Customer 3,Bengaluru,South,live3@acme.in,+919999900003\n"
        )
        files = {
            "file": ("customers.csv", csv_content.encode("utf-8"), "text/csv"),
        }
        data = {
            "mode": "upsert",
        }
        import_res = await client.post(
            "/v1/company/datasets/customers/imports",
            headers=headers,
            cookies=cookies,
            files=files,
            data=data,
        )
        assert import_res.status_code == 200, f"Import failed: {import_res.text}"

        # 3. Ask in chat: "How many customers are in each region?"
        chat_res = await client.post(
            "/v1/chat/query",
            headers=headers,
            cookies=cookies,
            json={
                "question": "How many customers are in each region?",
                "history": [],
            },
        )
        assert chat_res.status_code == 200, f"Chat query failed: {chat_res.text}"
        chat_data = chat_res.json()
        status = chat_data.get("status")
        answer = chat_data.get("answer", {})
        row_count = answer.get("row_count")
        chart_hint = answer.get("chart_hint")

        print("=== LIVE CHECK: ACME TRADERS ===")
        print(f"status: {status}")
        print(f"row_count: {row_count}")
        print(f"chart_hint: {chart_hint}")

        # 4. Log in as another company user (vikram@bharat.in)
        login_b = await client.post("/v1/auth/login", json={
            "email": "vikram@bharat.in",
            "password": "Demo@1234",
        })
        assert login_b.status_code == 200, f"Bharat login failed: {login_b.text}"
        csrf_b = login_b.cookies.get("csrf_token")
        cookies_b = dict(login_b.cookies)
        headers_b = {"X-CSRF-Token": csrf_b}

        # Ask the same question for Bharat Foods
        chat_b = await client.post(
            "/v1/chat/query",
            headers=headers_b,
            cookies=cookies_b,
            json={
                "question": "How many customers are in each region?",
                "history": [],
            },
        )
        assert chat_b.status_code == 200
        data_b = chat_b.json()
        answer_b = data_b.get("answer", {})

        print("\n=== LIVE CHECK: BHARAT FOODS (ISOLATION) ===")
        print(f"status: {data_b.get('status')}")
        print(f"detail: {answer_b.get('detail')}")
        print(f"rows: {answer_b.get('rows')}")

        # Ensure no Acme customers appear
        rows_str = str(answer_b.get("rows", []))
        assert "Live Customer" not in rows_str
        assert "Acme" not in rows_str
        print("CONFIRMED: Nothing from Acme appears for Bharat Foods!")

        # 5. Check completed
        print("Live check completed successfully.")

if __name__ == "__main__":
    asyncio.run(run_live_check())
