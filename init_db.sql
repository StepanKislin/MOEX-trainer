CREATE DATABASE IF NOT EXISTS `evriki-nto`
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE `evriki-nto`;

CREATE TABLE IF NOT EXISTS `users` (
  `id` int NOT NULL AUTO_INCREMENT,
  `username` varchar(50) NOT NULL,
  `email` varchar(255) NOT NULL,
  `password_hash` varchar(255) NOT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `last_login` timestamp NULL DEFAULT NULL,
  `is_active` tinyint(1) NOT NULL DEFAULT 1,
  `is_admin` tinyint(1) NOT NULL DEFAULT 0,
  `is_teacher` tinyint(1) NOT NULL DEFAULT 0,
  `room_joined_at` timestamp NULL DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_users_username` (`username`),
  UNIQUE KEY `uk_users_email` (`email`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `securities` (
  `id` int NOT NULL AUTO_INCREMENT,
  `secid` varchar(20) NOT NULL,
  `shortname` varchar(100) NOT NULL,
  `sector` varchar(100) DEFAULT NULL,
  `engine` varchar(20) DEFAULT 'stock',
  `market` varchar(20) DEFAULT 'shares',
  `board_default` varchar(10) DEFAULT 'TQBR',
  `currency` varchar(3) DEFAULT 'RUB',
  `lot_size` int NOT NULL DEFAULT 1,
  `is_active` tinyint(1) NOT NULL DEFAULT 1,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_securities_secid` (`secid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `portfolios` (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `name` varchar(100) NOT NULL DEFAULT 'Мой портфель',
  `description` text,
  `initial_balance` decimal(15,2) NOT NULL DEFAULT '1000000.00',
  `current_cash` decimal(15,2) NOT NULL DEFAULT '1000000.00',
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_portfolios_user` (`user_id`),
  CONSTRAINT `fk_portfolios_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `portfolio_items` (
  `id` int NOT NULL AUTO_INCREMENT,
  `portfolio_id` int NOT NULL,
  `security_id` int NOT NULL,
  `quantity` decimal(12,4) NOT NULL,
  `avg_buy_price` decimal(12,4) NOT NULL,
  `first_bought_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_portfolio_security` (`portfolio_id`, `security_id`),
  KEY `idx_portfolio_items_security` (`security_id`),
  CONSTRAINT `fk_portfolio_items_portfolio` FOREIGN KEY (`portfolio_id`) REFERENCES `portfolios` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_portfolio_items_security` FOREIGN KEY (`security_id`) REFERENCES `securities` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `portfolio_snapshots` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `portfolio_id` int NOT NULL,
  `snapshot_date` date NOT NULL,
  `total_value` decimal(15,2) NOT NULL,
  `cash_balance` decimal(15,2) NOT NULL,
  `invested_value` decimal(15,2) NOT NULL,
  `daily_change_pct` decimal(7,4) DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_snapshot_date` (`portfolio_id`, `snapshot_date`),
  CONSTRAINT `fk_snapshots_portfolio` FOREIGN KEY (`portfolio_id`) REFERENCES `portfolios` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `stress_scenarios` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(100) NOT NULL,
  `slug` varchar(80) NOT NULL,
  `start_date` date NOT NULL,
  `end_date` date NOT NULL,
  `description` text,
  `coefficients` json NOT NULL,
  `market_context` json DEFAULT NULL,
  `is_active` tinyint(1) NOT NULL DEFAULT 1,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_stress_slug` (`slug`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `transactions` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `portfolio_id` int NOT NULL,
  `security_id` int NOT NULL,
  `tx_type` enum('BUY','SELL','DEPOSIT','WITHDRAW') NOT NULL,
  `quantity` decimal(12,4) NOT NULL,
  `price` decimal(12,4) NOT NULL,
  `executed_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `market_price_at_tx` decimal(12,4) DEFAULT NULL,
  `notes` text,
  PRIMARY KEY (`id`),
  KEY `idx_transactions_portfolio` (`portfolio_id`, `executed_at`),
  KEY `idx_transactions_security` (`security_id`),
  CONSTRAINT `fk_transactions_portfolio` FOREIGN KEY (`portfolio_id`) REFERENCES `portfolios` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_transactions_security` FOREIGN KEY (`security_id`) REFERENCES `securities` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `room_sessions` (
  `id` int NOT NULL AUTO_INCREMENT,
  `teacher_id` int NOT NULL,
  `title` varchar(150) NOT NULL,
  `description` text,
  `room_code` varchar(20) NOT NULL,
  `mode` enum('practice','stress') NOT NULL DEFAULT 'practice',
  `scenario_id` int DEFAULT NULL,
  `starts_at` datetime NOT NULL,
  `ends_at` datetime NOT NULL,
  `initial_balance` decimal(15,2) NOT NULL DEFAULT '1000000.00',
  `is_active` tinyint(1) NOT NULL DEFAULT 1,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_room_code` (`room_code`),
  KEY `idx_room_sessions_teacher` (`teacher_id`),
  KEY `idx_room_sessions_scenario` (`scenario_id`),
  CONSTRAINT `fk_room_teacher` FOREIGN KEY (`teacher_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_room_scenario` FOREIGN KEY (`scenario_id`) REFERENCES `stress_scenarios` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `room_participants` (
  `id` int NOT NULL AUTO_INCREMENT,
  `room_id` int NOT NULL,
  `user_id` int NOT NULL,
  `joined_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `completed_at` timestamp NULL DEFAULT NULL,
  `portfolio_value` decimal(15,2) NOT NULL DEFAULT '0.00',
  `stress_value` decimal(15,2) NOT NULL DEFAULT '0.00',
  `total_return_pct` decimal(9,4) NOT NULL DEFAULT '0.0000',
  `sharpe_ratio` decimal(9,4) NOT NULL DEFAULT '0.0000',
  `score` decimal(15,4) NOT NULL DEFAULT '0.0000',
  `rank_position` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_room_participant` (`room_id`, `user_id`),
  KEY `idx_room_participants_user` (`user_id`),
  CONSTRAINT `fk_room_participants_room` FOREIGN KEY (`room_id`) REFERENCES `room_sessions` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_room_participants_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO `securities` (`secid`, `shortname`, `sector`, `currency`, `lot_size`, `is_active`)
VALUES
  ('SBER', 'Сбербанк', 'Финансы', 'RUB', 10, 1),
  ('GAZP', 'Газпром', 'Энергетика', 'RUB', 10, 1),
  ('LKOH', 'Лукойл', 'Нефть и газ', 'RUB', 1, 1),
  ('YDEX', 'Яндекс', 'IT', 'RUB', 1, 1),
  ('MGNT', 'Магнит', 'Ритейл', 'RUB', 1, 1),
  ('GMKN', 'Норникель', 'Металлы', 'RUB', 10, 1),
  ('AFLT', 'Аэрофлот', 'Транспорт', 'RUB', 10, 1),
  ('VTBR', 'ВТБ', 'Финансы', 'RUB', 10, 1),
  ('ROSN', 'Роснефть', 'Нефть и газ', 'RUB', 1, 1),
  ('NVTK', 'НОВАТЭК', 'Нефть и газ', 'RUB', 1, 1),
  ('TATN', 'Татнефть', 'Нефть и газ', 'RUB', 1, 1),
  ('CHMF', 'Северсталь', 'Металлы', 'RUB', 1, 1),
  ('PLZL', 'Полюс', 'Металлы', 'RUB', 1, 1),
  ('MOEX', 'Московская Биржа', 'Финансы', 'RUB', 10, 1),
  ('IRAO', 'Интер РАО', 'Энергетика', 'RUB', 100, 1),
  ('ALRS', 'АЛРОСА', 'Материалы', 'RUB', 10, 1),
  ('SNGS', 'Сургутнефтегаз', 'Нефть и газ', 'RUB', 100, 1),
  ('PHOR', 'ФосАгро', 'Химия', 'RUB', 1, 1),
  ('CHMK', 'ЧМК', 'Металлы', 'RUB', 10, 1),
  ('MTSS', 'МТС', 'Телеком', 'RUB', 10, 1),
  ('RASP', 'Распадская', 'Металлы', 'RUB', 10, 1)
ON DUPLICATE KEY UPDATE
  shortname = VALUES(shortname),
  sector = VALUES(sector),
  currency = VALUES(currency),
  lot_size = VALUES(lot_size),
  is_active = VALUES(is_active);

INSERT INTO `stress_scenarios` (`name`, `slug`, `start_date`, `end_date`, `description`, `coefficients`, `market_context`, `is_active`)
VALUES
  (
    'Валютный кризис 2014',
    'crisis-2014',
    '2014-01-01',
    '2014-12-31',
    'Падение рубля, снижение нефтяных котировок и давление на банковский и сырьевой сектор.',
    JSON_OBJECT('SBER', 0.58, 'GAZP', 0.52, 'LKOH', 0.62, 'YDEX', 1.18, 'MGNT', 0.78, 'GMKN', 0.64, 'AFLT', 0.48),
    NULL,
    1
  ),
  (
    'Пандемия 2020',
    'pandemic-2020',
    '2020-02-01',
    '2020-04-30',
    'Локдаун, падение спроса на перевозки и высокая волатильность по большинству акций.',
    JSON_OBJECT('SBER', 0.62, 'GAZP', 0.68, 'LKOH', 0.64, 'YDEX', 0.78, 'MGNT', 0.85, 'GMKN', 0.72, 'AFLT', 0.48),
    NULL,
    1
  ),
  (
    'Геополитический кризис 2022',
    'crisis-2022',
    '2022-02-01',
    '2022-12-31',
    'Санкции, остановка торгов и резкое изменение структуры спроса на рынке.',
    JSON_OBJECT('SBER', 0.32, 'GAZP', 0.38, 'LKOH', 0.55, 'YDEX', 0.42, 'MGNT', 0.68, 'GMKN', 0.58, 'AFLT', 0.18),
    NULL,
    1
  )
ON DUPLICATE KEY UPDATE
  description = VALUES(description),
  coefficients = VALUES(coefficients),
  is_active = VALUES(is_active);
