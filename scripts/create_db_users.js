// scripts/create_db_users.js
// Mongosh script to provision the three logical database users:
// 1. app_user (readWrite on app_data)
// 2. business_reader (read on business_data only; can NEVER write)
// 3. business_writer (readWrite on business_data for seeding/ingestion only)

db = db.getSiblingDB("app_data");
try {
  db.createUser({
    user: "app_user",
    pwd: "app_secure_password_123",
    roles: [{ role: "readWrite", db: "app_data" }]
  });
} catch (e) {
  print("app_user already exists or error: " + e);
}

db = db.getSiblingDB("business_data");
try {
  db.createUser({
    user: "business_reader",
    pwd: "reader_secure_password_123",
    roles: [{ role: "read", db: "business_data" }]
  });
} catch (e) {
  print("business_reader already exists or error: " + e);
}

try {
  db.createUser({
    user: "business_writer",
    pwd: "writer_secure_password_123",
    roles: [{ role: "readWrite", db: "business_data" }]
  });
} catch (e) {
  print("business_writer already exists or error: " + e);
}

print("Provisioned MongoDB credentials for app_data and business_data.");

