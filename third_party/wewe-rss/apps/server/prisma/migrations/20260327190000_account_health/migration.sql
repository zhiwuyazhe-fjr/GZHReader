ALTER TABLE `accounts`
  ADD COLUMN `consecutive_auth_failures` INT NOT NULL DEFAULT 0,
  ADD COLUMN `daily_request_count` INT NOT NULL DEFAULT 0,
  ADD COLUMN `daily_request_date` VARCHAR(32) NULL,
  ADD COLUMN `cooldown_until` INT NULL,
  ADD COLUMN `last_error` VARCHAR(2048) NULL,
  ADD COLUMN `last_success_at` DATETIME NULL;
