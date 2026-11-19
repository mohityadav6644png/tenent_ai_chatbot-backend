import asyncio
import sys
import uuid
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.main import app
from app.infrastructure.db.mongodb import connect_to_mongodb, close_mongodb_connection, users_collection
from app.core.security import create_email_verification_token, create_password_reset_token


async def run_auth_test_flow():
    print("=" * 60)
    print("STARTING COMPLETE AUTHENTICATION FLOW TEST")
    print("=" * 60)

    # 1. Connect DB
    await connect_to_mongodb()

    random_id = uuid.uuid4().hex[:6]
    test_user = f"tester_{random_id}"
    test_email = f"tester_{random_id}@example.com"
    test_password = "Password123!"
    new_password = "NewPassword456!"

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Step 1: Health check
        res = await client.get("/v1/health")
        assert res.status_code == 200, f"Health check failed: {res.text}"
        print(f"[OK] Health check passed: {res.json()}")

        # Step 2: Register user
        reg_payload = {
            "username": test_user,
            "email": test_email,
            "password": test_password,
            "name": f"Tester {random_id.upper()}",
        }
        res = await client.post("/v1/auth/register", json=reg_payload)
        assert res.status_code == 201, f"Register failed: {res.text}"
        reg_data = res.json()
        assert reg_data["username"] == test_user
        assert reg_data["is_verified"] is False
        print(f"[OK] 1. Register successful for user: {test_user} (is_verified={reg_data['is_verified']})")

        # Step 3: Duplicate register attempt (should fail 409)
        res = await client.post("/v1/auth/register", json=reg_payload)
        assert res.status_code == 409, f"Duplicate register should return 409: {res.text}"
        print("[OK] 2. Duplicate registration correctly blocked with 409 Conflict.")

        # Step 4: Verify Email via POST
        verify_token = create_email_verification_token(test_email)
        res = await client.post("/v1/auth/verify-email", json={"token": verify_token})
        assert res.status_code == 200, f"Email verification failed: {res.text}"
        print(f"[OK] 3. Verify Email successful: {res.json()['message']}")

        # Step 5: Login
        login_payload = {"email": test_email, "password": test_password}
        res = await client.post("/v1/auth/login", json=login_payload)
        assert res.status_code == 200, f"Login failed: {res.text}"
        tokens = res.json()
        access_token = tokens["access_token"]
        refresh_token = tokens["refresh_token"]
        assert tokens["user"]["is_verified"] is True
        print(f"[OK] 4. Login successful! Received access_token & refresh_token. User verified: {tokens['user']['is_verified']}")

        # Step 6: Get current user profile (/v1/auth/me)
        headers = {"Authorization": f"Bearer {access_token}"}
        res = await client.get("/v1/auth/me", headers=headers)
        assert res.status_code == 200, f"/me failed: {res.text}"
        profile = res.json()
        assert profile["username"] == test_user
        assert profile["email"] == test_email
        print(f"[OK] 5. /v1/auth/me profile fetched: {profile['name']} ({profile['email']})")

        # Step 7: Resend verification
        res = await client.post("/v1/auth/resend-verification", json={"email": test_email})
        assert res.status_code == 200, f"Resend verification failed: {res.text}"
        print(f"[OK] 6. Resend verification response: {res.json()['message']}")

        # Step 8: Refresh token
        res = await client.post("/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert res.status_code == 200, f"Refresh token failed: {res.text}"
        new_tokens = res.json()
        new_access_token = new_tokens["access_token"]
        new_refresh_token = new_tokens["refresh_token"]
        print("[OK] 7. Token refreshed successfully! New tokens obtained.")

        # Step 9: Forgot password
        res = await client.post("/v1/auth/forgot-password", json={"email": test_email})
        assert res.status_code == 200, f"Forgot password failed: {res.text}"
        print(f"[OK] 8. Forgot password initiated: {res.json()['message']}")

        # Step 10: Reset password
        user_in_db = await users_collection.find_one({"email": test_email})
        reset_token = create_password_reset_token(str(user_in_db["_id"]), test_email)
        res = await client.post(
            "/v1/auth/reset-password",
            json={"token": reset_token, "new_password": new_password},
        )
        assert res.status_code == 200, f"Reset password failed: {res.text}"
        print(f"[OK] 9. Reset password successful: {res.json()['message']}")

        # Step 11: Login with old password must fail (401)
        res = await client.post("/v1/auth/login", json={"email": test_email, "password": test_password})
        assert res.status_code == 401, "Old password should have been rejected with 401"
        print("[OK] 10. Login with OLD password rejected correctly (401 Unauthorized).")

        # Step 12: Login with NEW password must succeed
        res = await client.post("/v1/auth/login", json={"email": test_email, "password": new_password})
        assert res.status_code == 200, f"Login with new password failed: {res.text}"
        latest_tokens = res.json()
        active_access_token = latest_tokens["access_token"]
        print("[OK] 11. Login with NEW password succeeded!")

        # Step 13: Logout
        logout_headers = {"Authorization": f"Bearer {active_access_token}"}
        res = await client.post("/v1/auth/logout", headers=logout_headers)
        assert res.status_code == 200, f"Logout failed: {res.text}"
        print(f"[OK] 12. Logout successful: {res.json()['message']}")

        # Step 14: Access /v1/auth/me with logged-out token (must fail 401)
        res = await client.get("/v1/auth/me", headers=logout_headers)
        assert res.status_code == 401, "Revoked token should be rejected with 401"
        print(f"[OK] 13. Revoked token access rejected correctly (401: {res.json()['detail']})")

    # Cleanup test user
    await users_collection.delete_one({"email": test_email})
    await close_mongodb_connection()
    print("=" * 60)
    print("ALL 13 AUTH LIFECYCLE TESTS COMPLETED WITH 100% SUCCESS!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_auth_test_flow())
