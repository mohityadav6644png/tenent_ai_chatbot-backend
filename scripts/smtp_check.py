import os, sys, re, smtplib, ssl
from pathlib import Path
from email.message import EmailMessage

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

def read_env(path):
    data, dups = {}, set()
    for raw in path.read_text(encoding="utf-8-sig").splitlines():   # utf-8-sig removes a hidden BOM
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[7:]
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k in data and data[k] and not v:
            dups.add(k)                      # a later empty line must not erase a real value
            continue
        if k in data and data[k] and v:
            dups.add(k)
        data[k] = v
    return data, dups

if not ENV_PATH.exists():
    print("FAILED: .env not found at", ENV_PATH); sys.exit(1)

env, dups = read_env(ENV_PATH)

def pick(*names):
    for n in names:
        if env.get(n):
            return env[n]
    return ""

host = pick("SMTP_HOST", "MAIL_SERVER", "EMAIL_HOST") or "smtp.gmail.com"
port = int(pick("SMTP_PORT", "MAIL_PORT", "EMAIL_PORT") or 587)
user = pick("SMTP_USERNAME", "SMTP_USER", "MAIL_USERNAME", "EMAIL_HOST_USER")
pwd = pick("SMTP_PASSWORD", "MAIL_PASSWORD", "EMAIL_HOST_PASSWORD").replace(" ", "")
sender = pick("EMAIL_FROM_ADDRESS", "MAIL_FROM", "SMTP_FROM", "EMAIL_FROM", "FROM_EMAIL") or user
starttls = (pick("SMTP_USE_STARTTLS", "MAIL_STARTTLS") or "true").lower() == "true"
to = sys.argv[1]

print("--- what the script sees (names and yes/no only) ---")
for n in sorted(env):
    if re.search(r"SMTP|MAIL|FROM|USER", n, re.I):
        print(f"  {n:28} {'has value' if env[n] else 'EMPTY'}")
print("duplicate names in file:", sorted(dups) or "none")
print("host:", host, "| port:", port)
print("username found:", bool(user), "| password found:", bool(pwd), "| from found:", bool(sender))

msg = EmailMessage()
msg["From"], msg["To"], msg["Subject"] = sender, to, "SMTP test"
msg.set_content("It works.")
try:
    if port == 465:
        s = smtplib.SMTP_SSL(host, port, timeout=10, context=ssl.create_default_context())
    else:
        s = smtplib.SMTP(host, port, timeout=10)
        s.ehlo()
        if starttls:
            s.starttls(context=ssl.create_default_context()); s.ehlo()
    if user:
        s.login(user, pwd)
    s.send_message(msg); s.quit()
    print("SENT")
except Exception as e:
    print("FAILED:", type(e).__name__, "| code:", getattr(e, "smtp_code", None))
    err = getattr(e, "smtp_error", b"")
    if err:
        print("Server says:", err.decode(errors="ignore")[:100])