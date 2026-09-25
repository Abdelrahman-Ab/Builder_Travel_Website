BUILDER TRAVEL — VERCEL VERSION
================================
This is the Vercel/cloud edition of the approved V5 UI.
Unlike the local edition, it does NOT use SQLite or Tesseract installed on visitors' devices.

REQUIRED VERCEL ENVIRONMENT VARIABLES
1) DATABASE_URL
   PostgreSQL connection string. Recommended: create a Neon Postgres database and copy its pooled connection URL.
2) SECRET_KEY
   A long random secret, e.g. 64 random characters.
3) OCR_SPACE_API_KEY
   OCR.Space API key. OCR runs server-side; customers install nothing.

DEPLOY
- Create a new Vercel project and upload/import this folder.
- Framework preset: Other.
- Add the 3 environment variables above in Vercel Project Settings > Environment Variables.
- Deploy.
- First API request automatically creates the database tables and demo data.

ADMIN
URL: /admin
Email: admin@buildertravel.com
Password: Builder@2026
IMPORTANT: Change this password after the director demo / before real public use.

DATA
- Packages, bookings, passengers, waitlist and users persist in PostgreSQL.
- Uploaded passport files are stored privately in PostgreSQL BYTEA for this demo build, never as public static files.
- Admin passenger ZIP download includes Passenger-Data.txt, Passenger-Data.json and the original passport file.

PRODUCTION NOTE
Before accepting real customer passports at scale, move passport binaries to dedicated encrypted private object storage, add a formal retention policy, 2FA, audit review, backups, and production privacy/security controls.

V3 storage authentication
-------------------------
Private passport storage uses the Vercel Blob project connection with OIDC.
Required project connection/environment: BLOB_STORE_ID. Vercel injects and rotates
VERCEL_OIDC_TOKEN at runtime. Do not copy or persist VERCEL_OIDC_TOKEN in code.
BLOB_READ_WRITE_TOKEN is not required for the Vercel deployment; it remains a
fallback supported by the SDK for non-Vercel/local use.

V5: OCR upload now preserves the original passport filename and explicitly sends PDF/JPG/PNG filetype to OCR.Space.
