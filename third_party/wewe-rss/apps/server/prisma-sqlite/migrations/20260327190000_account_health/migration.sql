ALTER TABLE "accounts" ADD COLUMN "consecutive_auth_failures" INTEGER NOT NULL DEFAULT 0;
ALTER TABLE "accounts" ADD COLUMN "daily_request_count" INTEGER NOT NULL DEFAULT 0;
ALTER TABLE "accounts" ADD COLUMN "daily_request_date" TEXT;
ALTER TABLE "accounts" ADD COLUMN "cooldown_until" INTEGER;
ALTER TABLE "accounts" ADD COLUMN "last_error" TEXT;
ALTER TABLE "accounts" ADD COLUMN "last_success_at" DATETIME;
