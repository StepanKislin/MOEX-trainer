// Переключатель темы
(function initTheme() {
    const themeToggle = document.getElementById('theme-toggle');
    const html = document.documentElement;
    if (!themeToggle) return;

    const savedTheme = localStorage.getItem('theme') || 'light';
    html.setAttribute('data-theme', savedTheme);
    themeToggle.setAttribute('aria-label', savedTheme === 'dark' ? 'Светлая тема' : 'Тёмная тема');
    themeToggle.textContent = savedTheme === 'dark' ? '☀️' : '🌙';

    themeToggle.addEventListener('click', () => {
        const current = html.getAttribute('data-theme');
        const next = current === 'light' ? 'dark' : 'light';
        html.setAttribute('data-theme', next);
        localStorage.setItem('theme', next);
        themeToggle.textContent = next === 'dark' ? '☀️' : '🌙';
        themeToggle.setAttribute('aria-label', next === 'dark' ? 'Светлая тема' : 'Тёмная тема');
    });
})();

// Плавная анимация уведомлений: создаёт хост если нет, добавляет toast с анимацией появления/исчезновения
function showToast(message, type = 'info') {
    let host = document.getElementById('toast-host');

    if (!host) {
        host = document.createElement('div');
        host.id = 'toast-host';
        host.className = 'toast-host';
        document.body.appendChild(host);
    }

    const el = document.createElement('div');
    el.className = `toast toast--${type}`;
    el.setAttribute('role', 'status');
    el.textContent = message;
    host.appendChild(el);
    
    // Запускаем CSS-анимацию появления через requestAnimationFrame
    requestAnimationFrame(() => el.classList.add('toast--visible'));
    
    // Автоматическое удаление через 3.8 секунды с плавным исчезновением
    setTimeout(() => {
        el.classList.remove('toast--visible');
        el.addEventListener('transitionend', () => el.remove(), { once: true });
    }, 3800);
}

// Модальное окно с промисом — позволяет ждать результат (confirm/cancel) в async/await стиле
function openModal({ title, bodyHtml, confirmText = 'Подтвердить', cancelText = 'Отмена' }) {
    return new Promise((resolve) => {
        let closed = false; // Флаг предотвращения двойного закрытия
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.setAttribute('aria-labelledby', 'modal-title');
        overlay.innerHTML = `
            <div class="modal">
                <h2 id="modal-title" class="modal-title">${escapeHtml(title)}</h2>
                <div class="modal-body">${bodyHtml}</div>
                <div class="modal-actions">
                    <button type="button" class="btn btn-secondary modal-cancel">${escapeHtml(cancelText)}</button>
                    <button type="button" class="btn btn-primary modal-confirm">${escapeHtml(confirmText)}</button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);
        const confirmBtn = overlay.querySelector('.modal-confirm');
        const cancelBtn = overlay.querySelector('.modal-cancel');

        // Плавное удаление оверлея с поддержкой transitionend и fallback по таймеру
        function removeOverlay() {
            overlay.classList.add('modal-overlay--closing');
            const done = () => overlay.remove();
            overlay.addEventListener('transitionend', done, { once: true });
            setTimeout(done, 280); // Fallback если transitionend не сработает
        }

        // Единая точка завершения: предотвращает гонки событий, очищает слушатели, резолвит промис
        function finish(payload) {
            if (closed) return;
            closed = true;
            document.removeEventListener('keydown', onKey);
            removeOverlay();
            resolve(payload);
        }

        // Обработчик Confirm: извлекает значения из динамических полей (#buy-lots / #sell-qty)
        confirmBtn.addEventListener('click', () => {
            const buyLots = overlay.querySelector('#buy-lots');
            const sellQty = overlay.querySelector('#sell-qty');
            const payload = { ok: true };
            if (buyLots) payload.lots = parseInt(buyLots.value, 10);
            if (sellQty) payload.qty = parseInt(sellQty.value, 10);
            finish(payload);
        });
        
        cancelBtn.addEventListener('click', () => finish({ ok: false }));
        
        // Закрытие по клику на оверлей (не на модалку)
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) finish({ ok: false });
        });
        
        // Закрытие по Escape
        function onKey(e) {
            if (e.key === 'Escape') finish({ ok: false });
        }
        document.addEventListener('keydown', onKey);
        confirmBtn.focus(); // Доступность: фокус на кнопку подтверждения
    });
}

// Экранирование HTML-сущностей для защиты от XSS при вставке пользовательского контента
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

let stocksCache = [];
let tradeRequestInFlight = false;
let stockAnalyticsInFlight = false;
let selectedMarketSecid = null;
const MARKET_NEWS_QUERY = 'stock market OR MOEX OR inflation OR central bank rates';
const portfolioSnapshotCache = { data: null, ts: 0 };
const securityAnalyticsCache = new Map();
const chartRenderMode = 'candles';

function invalidateClientCaches() {
    portfolioSnapshotCache.data = null;
    portfolioSnapshotCache.ts = 0;
    securityAnalyticsCache.clear();
}

async function getPortfolioSnapshot(options = {}) {
    const forceRefresh = !!options.forceRefresh;
    const ttlMs = forceRefresh ? 0 : 8000;
    if (!forceRefresh && portfolioSnapshotCache.data && (Date.now() - portfolioSnapshotCache.ts) < ttlMs) {
        return portfolioSnapshotCache.data;
    }
    const query = forceRefresh ? '?refresh=1' : '';
    const portfolio = await requestTrainerApi(`/api/portfolio${query}`);
    portfolioSnapshotCache.data = portfolio;
    portfolioSnapshotCache.ts = Date.now();
    return portfolio;
}

// Заглушка для графика при отсутствии данных
function renderChartUnavailableState(svg, message = 'Недостаточно данных для графика') {
    if (!svg) return;
    svg.innerHTML = `<foreignObject width="100%" height="100%"><div xmlns="http://www.w3.org/1999/xhtml" class="chart-empty">${escapeHtml(message)}</div></foreignObject>`;
}

// Единая обёртка для API-запросов: централизованная обработка ошибок и парсинг JSON
async function requestTrainerApi(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok || data?.error) {
        throw new Error(data?.error || 'Ошибка запроса');
    }
    return data;
}

function formatRubles(n) {
    const value = Number(n);
    if (!Number.isFinite(value)) {
        return '0,00 ₽';
    }
    return value.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' ₽';
}

const formatMoney = formatRubles;
const fetchJson = requestTrainerApi;

// Формирование подписи бумаги: "ТИКЕР · Компания" или только тикер если названия нет
function formatSecurityLabel(secid, shortname) {
    const ticker = String(secid || '').trim();
    const company = String(shortname || '').trim();
    if (!company || company === ticker) {
        return ticker;
    }
    return `${ticker} · ${company}`;
}

function setBalanceSummary(cash, totalValue = null) {
    const balanceEl = document.getElementById('user-balance');
    if (balanceEl) {
        balanceEl.textContent = formatRubles(cash);
    }
    const sub = document.getElementById('user-balance-sub');
    if (sub) {
        if (totalValue != null && Number.isFinite(Number(totalValue))) {
            sub.textContent = 'Свободные средства · оценка портфеля: ' + formatRubles(totalValue);
        } else {
            sub.textContent = 'Свободные средства обновлены';
        }
    }
}


function formatPercent(value, digits = 2) {
    const number = Number(value);
    if (!Number.isFinite(number)) {
        return '0.00%';
    }
    return `${number >= 0 ? '+' : ''}${number.toFixed(digits)}%`;
}

function formatOptionalPercent(value, digits = 1) {
    const number = Number(value);
    if (!Number.isFinite(number)) {
        return '—';
    }
    return `${number.toFixed(digits)}%`;
}

function formatMonthYear(dateValue) {
    const date = new Date(dateValue);
    if (Number.isNaN(date.getTime())) {
        return '';
    }
    return date.toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' });
}

function formatDateTime(dateValue) {
    const date = new Date(dateValue);
    if (Number.isNaN(date.getTime())) {
        return '';
    }
    return date.toLocaleString('ru-RU', {
        day: '2-digit',
        month: 'long',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    });
}

function formatFullDate(dateValue) {
    const date = new Date(dateValue);
    if (Number.isNaN(date.getTime())) {
        return String(dateValue || '');
    }
    return date.toLocaleDateString('ru-RU', { day: '2-digit', month: 'long', year: 'numeric' });
}

function formatCompactDate(dateValue) {
    const date = new Date(dateValue);
    if (Number.isNaN(date.getTime())) {
        return '';
    }
    return date.toLocaleString('ru-RU', {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    });
}

function renderNewsFeed(containerId, payload) {
    const container = document.getElementById(containerId);
    if (!container) return;
    const articles = Array.isArray(payload?.articles) ? payload.articles : [];
    if (!articles.length) {
        container.innerHTML = `<p class="empty-hint">${escapeHtml(payload?.message || 'Новости не найдены')}</p>`;
        return;
    }
    container.innerHTML = articles
        .map((article) => `
            <article class="news-item">
                ${article.image_url ? `<img class="news-item__image" src="${escapeHtml(article.image_url)}" alt="news preview" loading="lazy" />` : ''}
                <a class="news-item__title" href="${escapeHtml(article.url || '#')}" target="_blank" rel="noopener noreferrer">
                    ${escapeHtml(article.title || 'Без заголовка')}
                </a>
                <div class="news-item__meta">
                    <span>${escapeHtml(article.source || 'Источник')}</span>
                    <span>${escapeHtml(formatCompactDate(article.published_at) || '')}</span>
                </div>
                <div class="news-item__chips">
                    <span class="news-chip">релевантность: ${Number(article.relevance_score || 0)}</span>
                    <span class="news-chip">качество: ${Number(article.quality_score || 0)}</span>
                </div>
                ${article.description ? `<p class="news-item__desc">${escapeHtml(article.description)}</p>` : ''}
            </article>
        `)
        .join('');
}

function renderAnalysis(containerSummaryId, containerDriversId, analysis, options = {}) {
    const summaryEl = document.getElementById(containerSummaryId);
    const driversEl = document.getElementById(containerDriversId);
    if (!summaryEl || !driversEl) return;
    const summary = analysis?.summary || options.emptySummary || 'Недостаточно данных для аналитики.';
    const drivers = Array.isArray(analysis?.drivers) ? analysis.drivers : [];
    summaryEl.textContent = summary;
    if (!drivers.length) {
        driversEl.innerHTML = '<li class="muted">Подробные драйверы временно недоступны.</li>';
        return;
    }
    driversEl.innerHTML = drivers.map((driver) => `<li>${escapeHtml(driver)}</li>`).join('');
}

function renderForecastFactors(diagnostics, analyticsData) {
    const grid = document.getElementById('forecast-factors-grid');
    if (!grid) return;
    if (!diagnostics) {
        grid.innerHTML = '<div class="forecast-card"><div class="forecast-card__label">Факторы</div><div class="forecast-card__sub">Нет данных для расчета факторов.</div></div>';
        return;
    }
    const factorCards = [
        { label: 'Модель', value: diagnostics.model || '—', sub: 'Мультифакторный прогноз с волатильностью и дивидендами' },
        { label: 'Ожидаемая доходность (год)', value: formatPercent(diagnostics.expected_annual_return_pct || 0), sub: 'Тренд + моментум + mean reversion + ставка + риск' },
        { label: 'Годовая волатильность', value: formatPercent(diagnostics.annual_volatility_pct || 0), sub: 'Оценка риска на базе истории доходностей' },
        { label: 'Уверенность', value: formatPercent((diagnostics.confidence || 0) * 100), sub: 'Чем выше шум рынка, тем ниже показатель' },
        { label: 'Дивидендная поддержка', value: formatPercent(diagnostics.dividend_support_pct || 0), sub: `${analyticsData?.volatility_level ? `Волатильность: ${analyticsData.volatility_level}` : ''}` },
        { label: '3м моментум', value: formatPercent(diagnostics.momentum_3m_pct || 0), sub: 'Импульс за последние ~63 торговых дня' },
        { label: 'Рост рынка (г/г)', value: formatPercent(diagnostics.market_growth_assumption_pct || 0), sub: `Инфляция: ${formatPercent(diagnostics.inflation_assumption_pct || 0)} · реальный рост: ${formatPercent(diagnostics.real_market_growth_pct || 0)}` },
    ];
    grid.innerHTML = factorCards
        .map((factor) => `
            <div class="forecast-card">
                <div class="forecast-card__label">${escapeHtml(factor.label)}</div>
                <div class="forecast-card__value">${escapeHtml(String(factor.value || '—'))}</div>
                <div class="forecast-card__sub">${escapeHtml(factor.sub || '')}</div>
            </div>
        `)
        .join('');
}

function renderForecastCards(containerId, forecast, options = {}) {
    const container = document.getElementById(containerId);
    if (!container) return;
    const labels = {
        month: '1 месяц',
        year: '1 год',
        ten_years: '10 лет',
    };
    const subtitles = options.subtitles || {};
    container.innerHTML = Object.entries(labels)
        .map(([key, label]) => `
            <div class="forecast-card">
                <div class="forecast-card__label">${label}</div>
                <div class="forecast-card__value">${formatRubles(forecast?.[key])}</div>
                <div class="forecast-card__sub">${subtitles[key] || ''}</div>
            </div>
        `)
        .join('');
}

// Преобразует исторические данные + прогноз в единую серию для отрисовки на графике
// Добавляет три прогнозные точки: +30 дней, +365 дней, +3650 дней от последней даты истории
function buildProjectionSeries(history, forecast) {
    const safeHistory = Array.isArray(history) ? history : [];
    const series = safeHistory.map((point) => ({
        date: point.date,
        close: Number(point.close),
        projected: false,
    }));
    if (!safeHistory.length || !forecast) {
        return series;
    }

    const lastDate = new Date(`${safeHistory[safeHistory.length - 1].date}T00:00:00`);
    if (Number.isNaN(lastDate.getTime())) {
        return series;
    }

    const projections = [
        { key: 'month', days: 30 },
        { key: 'year', days: 365 },
        { key: 'ten_years', days: 3650 },
    ];
    for (const projection of projections) {
        const value = Number(forecast[projection.key]);
        if (!Number.isFinite(value)) continue;
        const dt = new Date(lastDate);
        dt.setDate(dt.getDate() + projection.days);
        series.push({
            date: dt.toISOString().slice(0, 10),
            close: value,
            projected: true, // Флаг для визуального отличия прогноза от истории
            label: projection.key,
        });
    }
    return series;
}

// Нормализация данных свечей: если есть candles — используем их, иначе строим псевдо-свечи из истории
// Это нужно для единого формата отрисовки независимо от полноты данных с сервера
function normalizeCandles(candles, fallbackHistory = []) {
    if (Array.isArray(candles) && candles.length) {
        return candles
            .map((point) => ({
                date: point.date,
                open: Number(point.open),
                high: Number(point.high),
                low: Number(point.low),
                close: Number(point.close),
                volume: Number(point.volume || 0),
            }))
            .filter((point) =>
                Number.isFinite(point.open) &&
                Number.isFinite(point.high) &&
                Number.isFinite(point.low) &&
                Number.isFinite(point.close),
            );
    }

    // Fallback-логика: строим свечи из close-цен, если нет OHLCV-данных
    const safeHistory = Array.isArray(fallbackHistory) ? fallbackHistory : [];
    let previousClose = safeHistory.length ? Number(safeHistory[0].close) : 0;
    return safeHistory
        .map((point, index) => {
            const close = Number(point.close);
            const open = index === 0 ? close : previousClose; // Первая свеча: open=close, далее: open=предыдущий close
            previousClose = close;
            return {
                date: point.date,
                open,
                high: Math.max(open, close), // Если нет high/low, берём максимум из open/close
                low: Math.min(open, close),   // и минимум из open/close
                close,
                volume: 0, // Объём неизвестен в режиме fallback
            };
        })
        .filter((point) => Number.isFinite(point.close));
}

function formatVolume(value) {
    const number = Number(value || 0);
    if (!Number.isFinite(number)) {
        return '0';
    }
    if (number >= 1_000_000) {
        return `${(number / 1_000_000).toFixed(1)} млн`;
    }
    if (number >= 1_000) {
        return `${(number / 1_000).toFixed(1)} тыс`;
    }
    return `${Math.round(number)}`;
}

// Обновление мета-блока под графиком: дата, тикер, OHLC, объём с цветовым индикатором направления
function setChartMeta(metaId, candle, secid) {
    const meta = document.getElementById(metaId);
    if (!meta || !candle) return;
    const directionClass = Number(candle.close) >= Number(candle.open) ? 'positive' : 'negative';
    const prefix = meta.dataset.prefix ? `<span class="chart-meta__date">${escapeHtml(meta.dataset.prefix)}</span>` : '';
    meta.innerHTML = `
        ${prefix}
        <span class="chart-meta__date">${formatFullDate(candle.date)}</span>
        <span class="chart-meta__ticker">${escapeHtml(secid || '')}</span>
        <span>Откр.: ${formatRubles(candle.open)}</span>
        <span>Макс.: ${formatRubles(candle.high)}</span>
        <span>Мин.: ${formatRubles(candle.low)}</span>
        <span class="${directionClass}">Закр.: ${formatRubles(candle.close)}</span>
        <span>Объём: ${formatVolume(candle.volume)}</span>
    `;
}

// Основная функция отрисовки свечного графика в SVG — сложная математика координат и масштабирования
function renderLineChart(svgId, history, options = {}) {
    const svg = document.getElementById(svgId);
    if (!svg) return;
    const candles = normalizeCandles(options.candles, history);
    if (!candles || candles.length < 2) {
        renderChartUnavailableState(svg);
        return;
    }

    // Константы размеров и отступов для SVG-холста
    const width = 640;
    const height = 320;
    const padding = 24;
    const chartHeight = 220;
    const volumeHeight = 56;
    const volumeTop = height - padding - volumeHeight;
    
    // Конвертация строк дат в timestamp для корректного масштабирования по времени
    const points = candles
        .map((point) => ({
            ...point,
            timestamp: new Date(`${point.date}T00:00:00`).getTime(),
        }))
        .filter((point) => Number.isFinite(point.timestamp))
        .sort((a, b) => a.timestamp - b.timestamp);

    if (points.length < 2) {
        renderChartUnavailableState(svg);
        return;
    }

    // Расчёт диапазона цен для вертикального масштабирования
    const values = points.flatMap((point) => [point.low, point.high]).filter((value) => Number.isFinite(value));
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = Math.max(max - min, 1); // Защита от деления на ноль если все цены равны
    
    // Расчёт диапазона времени для горизонтального масштабирования
    const minTs = points[0].timestamp;
    const maxTs = points[points.length - 1].timestamp;
    const tsRange = Math.max(maxTs - minTs, 1);
    
    // Функция маппинга цены в Y-координату (инверсия: большая цена = меньший Y в SVG)
    const priceToY = (value) => padding + ((max - value) / range) * (chartHeight - padding);
    
    // Расчёт координат для каждой точки графика
    const coords = points.map((point) => {
        const x = padding + ((point.timestamp - minTs) / tsRange) * (width - padding * 2);
        return {
            ...point,
            x,
            yOpen: priceToY(point.open),
            yHigh: priceToY(point.high),
            yLow: priceToY(point.low),
            yClose: priceToY(point.close),
        };
    });

    const latest = coords[coords.length - 1];
    const stroke = options.stroke || 'var(--accent)';
    const shockTimestamp = options.shockDate ? new Date(`${options.shockDate}T00:00:00`).getTime() : null;
    const bullishFill = options.bullishFill || 'rgba(43, 143, 255, 0.9)';
    const bearishFill = options.bearishFill || 'rgba(255,255,255,0.95)';
    const wickStroke = 'rgba(15, 35, 55, 0.85)';
    const volumeMax = Math.max(...points.map((point) => point.volume || 0), 1);
    
    // Адаптивная ширина тела свечи: не уже 4px, не шире 14px, пропорционально доступному месту
    const bodyWidth = Math.max(4, Math.min(14, (width - padding * 2) / points.length * 0.6));

    // Разметка для маркера "шока" на графике (вертикальная пунктирная линия)
    let shockMarkup = '';
    if (Number.isFinite(shockTimestamp)) {
        const shockX = padding + ((Math.min(Math.max(shockTimestamp, minTs), maxTs) - minTs) / tsRange) * (width - padding * 2);
        const shockLabel = options.shockLabel || 'Начало шока';
        shockMarkup = `
            <line x1="${shockX}" y1="${padding}" x2="${shockX}" y2="${volumeTop - 6}" stroke="var(--danger)" stroke-width="2" stroke-dasharray="6 6"></line>
            <text x="${Math.min(shockX + 8, width - 140)}" y="${padding + 14}" fill="var(--danger)" font-size="12">${shockLabel}</text>
        `;
    }

    // Подписи по оси X: 5 равномерно распределённых дат
    const axisTicks = [0, 0.25, 0.5, 0.75, 1]
        .map((ratio) => {
            const idx = Math.min(coords.length - 1, Math.round((coords.length - 1) * ratio));
            const x = padding + ratio * (width - padding * 2);
            const label = formatMonthYear(coords[idx]?.date);
            return `<text x="${x}" y="${height - 4}" text-anchor="middle" fill="var(--text-muted)" font-size="11">${label}</text>`;
        })
        .join('');
    
    const chartMode = options.chartMode || chartRenderMode || 'candles';

    const candleMarkup = coords
        .map((point) => {
            const candleUp = point.close >= point.open;
            const fill = candleUp ? bullishFill : bearishFill;
            const bodyTop = Math.min(point.yOpen, point.yClose);
            const bodyHeight = Math.max(2, Math.abs(point.yOpen - point.yClose));
            const volumeBarHeight = Math.max(2, (Number(point.volume || 0) / volumeMax) * (volumeHeight - 8));
            const volumeY = height - padding - volumeBarHeight;
            const info = [
                formatFullDate(point.date),
                `Открытие: ${formatRubles(point.open)}`,
                `Макс.: ${formatRubles(point.high)}`,
                `Мин.: ${formatRubles(point.low)}`,
                `Закрытие: ${formatRubles(point.close)}`,
                `Объём: ${formatVolume(point.volume)}`,
            ].join(' · ');
            return `
                <g class="chart-candle" data-meta-id="${options.metaId || ''}" data-secid="${options.secid || ''}" data-date="${point.date}" data-open="${point.open}" data-high="${point.high}" data-low="${point.low}" data-close="${point.close}" data-volume="${point.volume}">
                    <title>${info}</title>
                    <line x1="${point.x}" y1="${point.yHigh}" x2="${point.x}" y2="${point.yLow}" stroke="${wickStroke}" stroke-width="1.6"></line>
                    <rect x="${point.x - bodyWidth / 2}" y="${bodyTop}" width="${bodyWidth}" height="${bodyHeight}" fill="${fill}" stroke="${wickStroke}" stroke-width="1.2" rx="1"></rect>
                    <rect x="${point.x - Math.max(2, bodyWidth * 0.35)}" y="${volumeY}" width="${Math.max(4, bodyWidth * 0.7)}" height="${volumeBarHeight}" fill="${candleUp ? 'rgba(22, 163, 74, 0.35)' : 'rgba(239, 68, 68, 0.28)'}" rx="1"></rect>
                </g>
            `;
        })
        .join('');

    const linePath = coords
        .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x} ${point.yClose}`)
        .join(' ');
    const lineMarkup = `
        <path d="${linePath}" fill="none" stroke="${stroke}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"></path>
        ${coords.map((point) => `
            <g class="chart-candle" data-meta-id="${options.metaId || ''}" data-secid="${options.secid || ''}" data-date="${point.date}" data-open="${point.open}" data-high="${point.high}" data-low="${point.low}" data-close="${point.close}" data-volume="${point.volume}">
                <circle cx="${point.x}" cy="${point.yClose}" r="3.2" fill="${stroke}"></circle>
            </g>
        `).join('')}
    `;

    const barMarkup = coords
        .map((point) => {
            const baselineY = priceToY(min);
            const y = Math.min(point.yClose, baselineY);
            const h = Math.max(Math.abs(baselineY - point.yClose), 2);
            const fill = point.close >= point.open ? 'rgba(16, 185, 129, 0.72)' : 'rgba(239, 68, 68, 0.7)';
            return `
                <g class="chart-candle" data-meta-id="${options.metaId || ''}" data-secid="${options.secid || ''}" data-date="${point.date}" data-open="${point.open}" data-high="${point.high}" data-low="${point.low}" data-close="${point.close}" data-volume="${point.volume}">
                    <rect x="${point.x - Math.max(3, bodyWidth * 0.35)}" y="${y}" width="${Math.max(6, bodyWidth * 0.7)}" height="${h}" rx="1.5" fill="${fill}"></rect>
                </g>
            `;
        })
        .join('');

    const chartMarkup = chartMode === 'line' ? lineMarkup : (chartMode === 'bars' ? barMarkup : candleMarkup);

    // Сборка финального SVG: сетка, маркеры, свечи, подписи
    svg.innerHTML = `
        <line x1="${padding}" y1="${volumeTop}" x2="${width - padding}" y2="${volumeTop}" stroke="rgba(148,163,184,0.22)" stroke-width="1"></line>
        <line x1="${padding}" y1="${padding}" x2="${padding}" y2="${volumeTop}" stroke="rgba(148,163,184,0.25)" stroke-width="1"></line>
        <line x1="${padding}" y1="${volumeTop}" x2="${padding}" y2="${height - padding}" stroke="rgba(148,163,184,0.18)" stroke-width="1"></line>
        ${[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
            const y = padding + ratio * (chartHeight - padding);
            return `<line x1="${padding}" y1="${y}" x2="${width - padding}" y2="${y}" stroke="rgba(148,163,184,0.18)" stroke-width="1"></line>`;
        }).join('')}
        ${shockMarkup}
        ${chartMarkup}
        <text x="${width - padding - 54}" y="${height - padding - 4}" fill="var(--text-muted)" font-size="10.5">Объём</text>
        ${axisTicks}
    `;

    // Обновление мета-блока последней свечой
    setChartMeta(options.metaId, latest, options.secid);

    // Интерактив: при наведении на свечу обновляем мета-блок с данными этой свечи
    svg.querySelectorAll('.chart-candle').forEach((candle) => {
        candle.addEventListener('mouseenter', () => {
            setChartMeta(
                candle.dataset.metaId,
                {
                    date: candle.dataset.date,
                    open: Number(candle.dataset.open),
                    high: Number(candle.dataset.high),
                    low: Number(candle.dataset.low),
                    close: Number(candle.dataset.close),
                    volume: Number(candle.dataset.volume),
                },
                candle.dataset.secid,
            );
        });
    });
}

// Обновление мета-блока истории портфеля: дата, стоимость, прибыль, доходность, коэффициент Шарпа
function setPortfolioHistoryMeta(metaId, point) {
    const meta = document.getElementById(metaId);
    if (!meta || !point) return;
    const profitClass = Number(point.profit_loss || 0) >= 0 ? 'positive' : 'negative';

    meta.innerHTML = `
        <span class="chart-meta__date">${formatFullDate(point.date)}</span>
        <span class="chart-meta__ticker">ПОРТФЕЛЬ</span>
        <span>Стоимость: ${formatRubles(point.close)}</span>
        <span class="${profitClass}">Прибыль / убыток: ${formatRubles(point.profit_loss || 0)}</span>
        <span class="${profitClass}">Доходность: ${formatPercent(point.profit_loss_pct || 0)}</span>
        <span>Коэффициент устойчивости: ${Number(point.sharpe_ratio || 0).toFixed(2)}</span>
    `;
}

// Отрисовка гистограммы истории портфеля: столбцы с цветовой индикацией прибыли/убытка
function renderPortfolioHistoryChart(svgId, history, options = {}) {
    const svg = document.getElementById(svgId);

    if (!svg) return;

    // Нормализация и валидация входных данных
    const points = (Array.isArray(history) ? history : [])
        .map((point) => ({
            ...point,
            timestamp: new Date(`${point.date}T00:00:00`).getTime(),
            close: Number(point.close),
            profit_loss: Number(point.profit_loss || 0),
            profit_loss_pct: Number(point.profit_loss_pct || 0),
            sharpe_ratio: Number(point.sharpe_ratio || 0),
        }))
        .filter((point) => Number.isFinite(point.timestamp) && Number.isFinite(point.close))
        .sort((a, b) => a.timestamp - b.timestamp);

    if (!points.length) {
        renderChartUnavailableState(svg, 'Пока нет данных для истории портфеля');
        return;
    }

    // Параметры холста и расчёт масштабов
    const width = 640;
    const height = 320;
    const padding = 24;
    const chartBottom = height - 44;
    
    // Диапазон цен с запасом 15% снизу для визуального комфорта
    const minValue = Math.min(...points.map((point) => point.close));
    const maxValue = Math.max(...points.map((point) => point.close));
    const range = Math.max(maxValue - minValue, Math.max(maxValue * 0.03, 1));
    const baseline = Math.max(0, minValue - range * 0.15);
    const chartRange = Math.max(maxValue - baseline, 1);
    
    // Адаптивная ширина столбцов: от 14 до 48px в зависимости от количества точек
    const barWidth = Math.max(14, Math.min(48, ((width - padding * 2) / points.length) * 0.72));

    // Функция маппинга значения цены в Y-координату
    const valueToY = (value) => padding + ((maxValue - value) / chartRange) * (chartBottom - padding);
    
    // Расчёт координат для каждой точки
    const coords = points.map((point, index) => {
        const step = points.length === 1 ? 0 : index / (points.length - 1);
        const x = padding + step * (width - padding * 2);
        const y = valueToY(point.close);
        return { ...point, x, y };
    });

    // Подписи по оси X: начало, середина, конец периода
    const tickLabels = [0, 0.5, 1]
        .map((ratio) => {
            const idx = Math.min(coords.length - 1, Math.round((coords.length - 1) * ratio));
            const point = coords[idx];
            return `<text x="${point.x}" y="${height - 8}" text-anchor="middle" fill="var(--text-muted)" font-size="11">${formatFullDate(point.date)}</text>`;
        })
        .join('');

    // Генерация SVG-элементов столбцов с цветовой индикацией прибыли/убытка
    const bars = coords.map((point) => {
        const barHeight = Math.max(chartBottom - point.y, 6); // Минимальная высота 6px
        const fill = Number(point.profit_loss || 0) >= 0 ? 'rgba(5, 150, 105, 0.72)' : 'rgba(220, 38, 38, 0.72)';
        return `
            <g class="chart-bar" data-meta-id="${options.metaId || ''}" data-date="${point.date}" data-close="${point.close}" data-profit-loss="${point.profit_loss}" data-profit-loss-pct="${point.profit_loss_pct}" data-sharpe-ratio="${point.sharpe_ratio}">
                <title>${formatFullDate(point.date)} · Стоимость: ${formatRubles(point.close)} · Прибыль / убыток: ${formatRubles(point.profit_loss)}</title>
                <rect x="${point.x - barWidth / 2}" y="${point.y}" width="${barWidth}" height="${barHeight}" rx="6" fill="${fill}"></rect>
            </g>
        `;
    }).join('');

    // Сборка финального SVG
    svg.innerHTML = `
        <line x1="${padding}" y1="${chartBottom}" x2="${width - padding}" y2="${chartBottom}" stroke="rgba(148,163,184,0.28)" stroke-width="1"></line>
        ${[0, 0.33, 0.66, 1].map((ratio) => {
            const y = padding + ratio * (chartBottom - padding);
            return `<line x1="${padding}" y1="${y}" x2="${width - padding}" y2="${y}" stroke="rgba(148,163,184,0.14)" stroke-width="1"></line>`;
        }).join('')}
        ${bars}
        ${tickLabels}
    `;

    // Инициализация мета-блока последней точкой
    setPortfolioHistoryMeta(options.metaId, coords[coords.length - 1]);
    
    // Интерактив: обновление мета-блока при наведении на столбец
    svg.querySelectorAll('.chart-bar').forEach((bar) => {
        bar.addEventListener('mouseenter', () => {
            setPortfolioHistoryMeta(bar.dataset.metaId, {
                date: bar.dataset.date,
                close: Number(bar.dataset.close),
                profit_loss: Number(bar.dataset.profitLoss),
                profit_loss_pct: Number(bar.dataset.profitLossPct),
                sharpe_ratio: Number(bar.dataset.sharpeRatio),
            });
        });
    });
}

// Обновление визуального состояния коэффициента Шарпа: текст, цвет, классы карточек
function applySharpeState(sharpeValue) {
    const value = Number(sharpeValue || 0);
    const sharpeEl = document.getElementById('sharpe-ratio');

    if (sharpeEl) {
        sharpeEl.textContent = value.toFixed(2);
        sharpeEl.classList.remove('positive', 'negative', 'muted');
        sharpeEl.classList.add(value > 0 ? 'positive' : value < 0 ? 'negative' : 'muted');
    }

    const sharpeCard = document.getElementById('sharpe-card');
    if (sharpeCard) {
        sharpeCard.classList.remove('summary-card--positive', 'summary-card--negative');
        if (value > 0) {
            sharpeCard.classList.add('summary-card--positive');
        } else if (value < 0) {
            sharpeCard.classList.add('summary-card--negative');
        }
    }

    const statSharpe = document.getElementById('stat-sharpe');
    if (statSharpe) {
        statSharpe.textContent = value.toFixed(2);
        statSharpe.classList.remove('stat-value--positive', 'stat-value--negative');
        statSharpe.classList.add(value >= 0 ? 'stat-value--positive' : 'stat-value--negative');
    }

    const sharpeHint = document.getElementById('sharpe-hint');
    if (sharpeHint) {
        sharpeHint.textContent = '';
    }
}

// Отправка торговой операции на сервер с обновлением баланса и портфеля
async function submitPortfolioTrade(url, { secid, quantity, successMessage }) {
    const result = await requestTrainerApi(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ secid, quantity }),
    });
    invalidateClientCaches();
    setBalanceSummary(result.cash_remaining);
    showToast(successMessage(result), 'success');
    await loadPortfolio();
}

// Загрузка аналитики по бумаге: динамический выбор контейнеров в зависимости от контекста (рынок/портфель)
async function loadSecurityAnalytics(secid, context = 'market') {
    if (stockAnalyticsInFlight) return;
    stockAnalyticsInFlight = true;
    try {
        const cacheKey = String(secid || '').toUpperCase();
        const cached = securityAnalyticsCache.get(cacheKey);
        const nowTs = Date.now();
        let data;
        if (cached && (nowTs - cached.ts) < 45000) {
            data = cached.data;
        } else {
            data = await requestTrainerApi(`/api/securities/${encodeURIComponent(secid)}/analytics`);
            securityAnalyticsCache.set(cacheKey, { ts: nowTs, data });
        }
        if (context === 'market') {
            selectedMarketSecid = data.secid;
            document.querySelectorAll('.security-row').forEach((row) => {
                row.classList.toggle('security-row--active', row.dataset.secid === data.secid);
            });
        }

        // Динамический выбор ID элементов: разные префиксы для рынка и портфеля
        const panelId = context === 'portfolio' ? `portfolio-detail-row-${secid}` : 'market-chart-panel';
        const titleId = context === 'portfolio' ? `portfolio-chart-title-${secid}` : 'market-chart-title';
        const subtitleId = context === 'portfolio' ? `portfolio-chart-subtitle-${secid}` : 'market-chart-subtitle';
        const metaId = context === 'portfolio' ? `portfolio-chart-meta-${secid}` : 'market-chart-meta';
        const svgId = context === 'portfolio' ? `portfolio-chart-svg-${secid}` : 'market-chart-svg';
        const forecastId = context === 'portfolio' ? `portfolio-position-forecast-grid-${secid}` : 'market-forecast-grid';
        
        const panel = document.getElementById(panelId);
        if (panel) {
            if (context === 'portfolio') {
                // В портфеле: скрываем все детальные строки, показываем только текущую
                document.querySelectorAll('.position-detail-row').forEach((row) => {
                    if (row.id !== panelId) row.hidden = true;
                });
                panel.hidden = false;
            } else {
                // На рынке: просто показываем панель и скроллим к ней
                panel.hidden = false;
                panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        }
        const title = document.getElementById(titleId);
        if (title) title.textContent = `${formatSecurityLabel(data.secid, data.shortname)}: история цены`;
        if (context === 'market') {
            const selectedTitle = document.getElementById('selected-security-title');
            if (selectedTitle) {
                selectedTitle.textContent = `${formatSecurityLabel(data.secid, data.shortname)} · ${formatRubles(data.current_price)}`;
            }
            const selectedSubtitle = document.getElementById('selected-security-subtitle');
            if (selectedSubtitle) {
                selectedSubtitle.textContent = `Сектор: ${data.sector || '—'}. Дивиденды: ${formatOptionalPercent(data.dividend_yield)}. Волатильность: ${data.volatility_level || 'не указана'}.`;
            }
            renderForecastFactors(data.forecast_diagnostics, data);
            renderNewsFeed('selected-news-list', data.related_news || { articles: [] });
            const selectedNewsQuery = document.getElementById('selected-news-query');
            if (selectedNewsQuery) {
                const queryText = data.related_news?.query || `${data.secid} ${data.shortname || ''}`.trim();
                selectedNewsQuery.textContent = `Источник: новости по акции ${data.secid}. Поисковый запрос: ${queryText}`;
            }
            const analysisCard = document.getElementById('selected-security-analysis-card');
            if (analysisCard) analysisCard.hidden = false;
            renderAnalysis('selected-security-analysis-summary', 'selected-security-analysis-drivers', data.analysis, {
                emptySummary: 'Подробная аналитика появится после накопления данных.',
            });
        }
        const subtitle = document.getElementById(subtitleId);
        
        // Расчёт дат для прогнозов от последней точки истории
        const anchorDate = data.history?.length
            ? new Date(`${data.history[data.history.length - 1].date}T00:00:00`)
            : new Date();
        const forecastMonth = new Date(anchorDate);
        forecastMonth.setDate(forecastMonth.getDate() + 30);
        const forecastYear = new Date(anchorDate);
        forecastYear.setDate(forecastYear.getDate() + 365);
        const forecastTenYears = new Date(anchorDate);
        forecastTenYears.setDate(forecastTenYears.getDate() + 3650);
        
        if (subtitle) {
            subtitle.textContent = context === 'portfolio'
                ? `Средняя цена покупки: ${formatRubles(data.position?.avg_buy_price || 0)}. Ниже история и прогноз позиции.`
                : 'Динамика цены на Московской бирже, текущая оценка и прогнозные точки.';
        }
        
        // Отрисовка графика с переданными опциями
        renderLineChart(svgId, data.history, {
            candles: data.candles,
            metaId,
            secid: data.secid,
            stroke: 'var(--accent)',
            currentLabel: 'Текущая',
        });
        
        // Формирование подписей для карточек прогноза: разные для портфеля и рынка
        const subtitles = context === 'portfolio' && data.position
            ? {
                month: `${formatMonthYear(forecastMonth)} · позиция ${formatRubles(data.position.month)}`,
                year: `${formatMonthYear(forecastYear)} · позиция ${formatRubles(data.position.year)}`,
                ten_years: `${formatMonthYear(forecastTenYears)} · позиция ${formatRubles(data.position.ten_years)}`,
            }
            : {
                month: `${formatMonthYear(forecastMonth)} · прогноз цены`,
                year: `${formatMonthYear(forecastYear)} · прогноз цены`,
                ten_years: `${formatMonthYear(forecastTenYears)} · долгосрочный прогноз`,
            };
        renderForecastCards(forecastId, data.forecast, { subtitles });
    } 
    catch (error) {
        showToast(error.message || 'Ошибка загрузки графика', 'error');
    } 
    finally {
        stockAnalyticsInFlight = false;
    }
}

// Загрузка истории бумаги в рамках стресс-сценария с отрисовкой графика и маркером "шока"
async function loadStressSecurityHistory(scenarioSlug, secid) {
    try {
        const data = await requestTrainerApi(`/api/stress/${encodeURIComponent(scenarioSlug)}/${encodeURIComponent(secid)}/history`);
        
        // Показываем только детальную строку текущей бумаги, скрываем остальные
        document.querySelectorAll('.stress-detail-row').forEach((row) => {
            if (row.id !== `stress-detail-row-${secid}`) row.hidden = true;
        });
        const panel = document.getElementById(`stress-detail-row-${secid}`);
        if (panel) panel.hidden = false;
        
        const title = document.getElementById(`stress-chart-title-${secid}`);
        if (title) title.textContent = `${formatSecurityLabel(data.secid, data.shortname)}: график в период сценария`;
        const subtitle = document.getElementById(`stress-chart-subtitle-${secid}`);
        const metaId = `stress-chart-meta-${secid}`;
        if (subtitle) {
            const rangeText = data.start_date && data.end_date
                ? `${formatFullDate(data.start_date)} — ${formatFullDate(data.end_date)}`
                : data.scenario_name;
            subtitle.textContent = `${rangeText}. На графике есть запас до шока, чтобы было видно резкое движение вниз.`;
        }
        const svg = document.getElementById(`stress-chart-svg-${secid}`);
        if (data.chart_available) {
            // Отрисовка графика с маркером шока
            renderLineChart(`stress-chart-svg-${secid}`, data.history, {
                candles: data.candles,
                metaId,
                secid: data.secid,
                stroke: 'var(--accent)',
                shockDate: data.shock_date,
                shockLabel: 'Точка шока',
                currentLabel: 'Финал периода',
            });
        } else if (svg) {
            // Заглушка если график недоступен
            renderChartUnavailableState(svg, data.chart_message || 'График недоступен');
            const meta = document.getElementById(metaId);
            if (meta) {
                meta.innerHTML = `<span class="negative">${escapeHtml(data.chart_message || 'График недоступен')}</span>`;
            }
        }
        const grid = document.getElementById(`stress-explanation-grid-${secid}`);
        if (grid) {
            const drivers = Array.isArray(data.analysis?.drivers) ? data.analysis.drivers : [];
            const sentiment = data.analysis?.news_sentiment?.sentiment || 'neutral';
            const sentimentLabel = sentiment === 'positive' ? 'позитивный' : (sentiment === 'negative' ? 'негативный' : 'смешанный');
            const stressNews = Array.isArray(data.stress_news?.articles) ? data.stress_news.articles.slice(0, 3) : [];
            const stressDirectionClass = data.stress_direction === 'up'
                ? 'stress-note-card--up'
                : (data.stress_direction === 'down' ? 'stress-note-card--down' : '');
            grid.innerHTML = `
                <div class="forecast-card stress-note-card ${stressDirectionClass}" style="grid-column: 1 / -1;">
                    <div class="forecast-card__label">Комментарий по сценарию</div>
                    <div class="forecast-card__sub">${escapeHtml(data.explanation || '')}</div>
                    <div class="forecast-card__sub" style="margin-top:0.5rem;">Изменение в периоде: ${Number(data.stress_change_pct || 0) >= 0 ? '+' : ''}${Number(data.stress_change_pct || 0).toFixed(2)}%.</div>
                    <div class="forecast-card__sub" style="margin-top:0.5rem;">Новостной фон: ${escapeHtml(sentimentLabel)}.</div>
                    ${data.analysis?.summary ? `<div class="forecast-card__sub" style="margin-top:0.5rem;">${escapeHtml(data.analysis.summary)}</div>` : ''}
                    ${drivers.length ? `<ul class="analysis-list">${drivers.map((driver) => `<li>${escapeHtml(driver)}</li>`).join('')}</ul>` : ''}
                    ${stressNews.length ? `
                        <div class="forecast-card__sub" style="margin-top:0.5rem;">Связанные новости:</div>
                        <ul class="analysis-list">
                            ${stressNews.map((article) => `<li><a href="${escapeHtml(article.url || '#')}" target="_blank" rel="noopener noreferrer">${escapeHtml(article.title || 'Без заголовка')}</a></li>`).join('')}
                        </ul>
                    ` : ''}
                    ${data.chart_available ? '' : `<div class="forecast-card__sub negative" style="margin-top:0.6rem;">${escapeHtml(data.chart_message || '')}</div>`}
                </div>
            `;
        }
    } 
    catch (error) {
        showToast(error.message || 'Ошибка загрузки исторического графика', 'error');
    }
}

async function loadMarketNews(query = MARKET_NEWS_QUERY) {
    try {
        const data = await requestTrainerApi(`/api/news?q=${encodeURIComponent(query)}&limit=12`);
        const queryLabel = document.getElementById('market-news-query');
        if (queryLabel) {
            queryLabel.textContent = `Запрос: ${data.query || query} (общерыночные новости)`;
        }
        renderNewsFeed('market-news-list', data);
    } catch (error) {
        renderNewsFeed('market-news-list', { articles: [], message: error.message || 'Не удалось загрузить новости' });
    }
}

async function updateGlobalTradeStats() {
    const profitEl = document.getElementById('global-trade-profit');
    const metaEl = document.getElementById('global-trade-meta');
    if (!profitEl || !metaEl) return;
    try {
        const portfolio = await getPortfolioSnapshot();
        const tradeStats = portfolio.trade_stats || {};
        const totalTradeProfit = Number(tradeStats.total_profit || 0);
        const sign = totalTradeProfit >= 0 ? '+' : '';
        profitEl.textContent = `${sign}${formatRubles(totalTradeProfit)}`;
        profitEl.classList.remove('positive', 'negative', 'muted');
        profitEl.classList.add(totalTradeProfit >= 0 ? 'positive' : 'negative');
        metaEl.textContent = `Сделки: покупок ${tradeStats.buy_count || 0}, продаж ${tradeStats.sell_count || 0}, win-rate продаж ${Number(tradeStats.sell_win_rate_pct || 0).toFixed(1)}%`;
    } catch (_error) {
        profitEl.textContent = '—';
        profitEl.classList.remove('positive', 'negative');
        profitEl.classList.add('muted');
        metaEl.textContent = 'Требуется вход для расчета статистики';
    }
}

// Загрузка списка бумаг для торговли: скелетон-загрузка, кэширование, обработка ошибок
async function loadStocks(options = {}) {
    const container = document.getElementById('stocks-container');
    if (!container) return;

    // Показ скелетона пока грузятся данные
    container.innerHTML = '<div class="skeleton-grid" aria-busy="true">' + Array(6).fill('<div class="skeleton-card"></div>').join('') + '</div>';

    try {
        const query = options.forceRefresh ? '?refresh=1' : '';
        const stocks = await requestTrainerApi(`/api/securities${query}`);
        stocksCache = stocks; // Кэшируем для быстрого фильтра
        renderStocks(stocks);
        if ((options.forceSelect || !selectedMarketSecid) && stocks.length) {
            loadSecurityAnalytics(stocks[0].secid, 'market');
        } else if (selectedMarketSecid) {
            const stillExists = stocks.some((s) => s.secid === selectedMarketSecid);
            if (stillExists) {
                document.querySelectorAll('.security-row').forEach((row) => {
                    row.classList.toggle('security-row--active', row.dataset.secid === selectedMarketSecid);
                });
            }
        }
        await updateBalance(options);
        await updateGlobalTradeStats();
    } 
    catch (error) {
        console.error('Ошибка загрузки акций:', error);
        container.innerHTML = '<p class="page-error">Не удалось загрузить котировки. Проверьте сеть и попробуйте снова.</p>';
        showToast('Ошибка загрузки акций', 'error');
    }
}

// Рендер карточек бумаг с обработчиками покупок и аналитики
function renderStocks(stocks) {
    const container = document.getElementById('stocks-container');
    if (!container) return;

    const marketPanel = document.getElementById('market-chart-panel');

    if (!stocks.length) {
        container.innerHTML = '<p class="empty-hint">Нет бумаг по заданному фильтру.</p>';
        if (marketPanel) {
            marketPanel.hidden = true;
        }
        return;
    }

    container.innerHTML = stocks.map((stock) => `
        <article class="security-row ${selectedMarketSecid === stock.secid ? 'security-row--active' : ''}" data-secid="${escapeHtml(stock.secid)}">
            <button type="button" class="security-row__main" data-analytics="${escapeHtml(stock.secid)}">
                <div class="security-row__top">
                    <span class="security-row__ticker">${escapeHtml(stock.secid)}</span>
                    <span class="security-row__price ${stock.is_price_missing ? 'negative' : ''}">${escapeHtml(stock.price_label || formatRubles(stock.price))}</span>
                </div>
                <div class="security-row__name">${escapeHtml(stock.shortname || '')}</div>
                <div class="security-row__meta">
                    <span>${escapeHtml(stock.sector || 'Другое')}</span>
                    <span>Лот: ${stock.lot_size}</span>
                    <span>Див: ${formatOptionalPercent(stock.dividend_yield)}</span>
                    <span>${escapeHtml(stock.volatility_level || 'не указана')}</span>
                </div>
            </button>
            <div class="security-row__actions">
                <button type="button" class="btn btn-buy btn-sm" data-buy="${escapeHtml(stock.secid)}" data-price="${stock.price}" data-lot="${stock.lot_size}" ${stock.is_price_missing ? 'disabled' : ''}>
                    Купить
                </button>
            </div>
        </article>
    `).join('');

    // Обработчики кнопок "Купить"
    container.querySelectorAll('[data-buy]').forEach((btn) => {
        btn.addEventListener('click', () => {
            buyStock(btn.dataset.buy, parseFloat(btn.dataset.price), parseInt(btn.dataset.lot, 10));
        });
    });

    // Обработчики кнопок аналитики
    container.querySelectorAll('[data-analytics]').forEach((btn) => {
        btn.addEventListener('click', () => loadSecurityAnalytics(btn.dataset.analytics, 'market'));
    });
}

// Фильтрация кэшированных бумаг по поисковому запросу (тиккер, название, сектор)
function filterStocks(query) {
    const q = (query || '').trim().toLowerCase();
    if (!q) {
        renderStocks(stocksCache);
        return;
    }
    const filtered = stocksCache.filter(
        (s) =>
            (s.secid && s.secid.toLowerCase().includes(q)) ||
            (s.shortname && String(s.shortname).toLowerCase().includes(q)) ||
            (s.sector && String(s.sector).toLowerCase().includes(q))
    );
    renderStocks(filtered);
}

// Загрузка портфеля: обновление сводных показателей, позиций, прогнозов и истории
async function loadPortfolio(options = {}) {
    try {
        const portfolio = await getPortfolioSnapshot({ forceRefresh: !!options.forceRefresh });

        // Обновление основных метрик портфеля
        const totalEl = document.getElementById('total-value');
        if (totalEl) {
            totalEl.textContent = formatRubles(portfolio.total_value);
        }

        const profitEl = document.getElementById('total-profit');
        if (profitEl) {
            const sign = portfolio.total_profit >= 0 ? '+' : '';
            profitEl.textContent = sign + formatRubles(portfolio.total_profit);
            profitEl.classList.remove('positive', 'negative', 'muted');
            profitEl.classList.add(portfolio.total_profit >= 0 ? 'positive' : 'negative');
        }
        const profitCard = document.getElementById('profit-card');
        if (profitCard) {
            profitCard.classList.remove('summary-card--positive', 'summary-card--negative');
            profitCard.classList.add(portfolio.total_profit >= 0 ? 'summary-card--positive' : 'summary-card--negative');
        }

        const pctEl = document.getElementById('total-profit-pct');
        if (pctEl && typeof portfolio.total_profit_pct === 'number') {
            const p = portfolio.total_profit_pct;
            pctEl.textContent = (p >= 0 ? '+' : '') + p.toFixed(2) + '%';
            pctEl.classList.remove('positive', 'negative', 'muted');
            pctEl.classList.add(p >= 0 ? 'positive' : 'negative');
        }

        const cashEl = document.getElementById('portfolio-cash');
        if (cashEl) {
            cashEl.textContent = formatRubles(portfolio.cash);
        }

        const sharpeValue = Number(portfolio.sharpe_ratio || 0);
        applySharpeState(sharpeValue);
        const diversificationStatus = document.getElementById('diversification-status');
        if (diversificationStatus) {
            diversificationStatus.textContent = `${portfolio.diversification_message} Сейчас в портфеле: ${portfolio.assets_count} компаний.`;
            diversificationStatus.classList.remove('positive', 'negative', 'muted');
            diversificationStatus.classList.add(portfolio.assets_rule_ok ? 'positive' : 'negative');
        }

        // Рендер таблицы позиций портфеля
        const positionsBody = document.getElementById('positions-body');
        if (positionsBody) {
            if (!portfolio.positions || portfolio.positions.length === 0) {
                positionsBody.innerHTML = `
                    <tr class="empty-row">
                        <td colspan="8">
                            <div class="table-empty">Пока нет позиций. <a href="/">Купить акции</a></div>
                        </td>
                    </tr>`;
            } else {
                positionsBody.innerHTML = portfolio.positions
                    .map(
                        (pos) => `
                <tr class="position-row">
                    <td data-label="Акция"><button type="button" class="ticker-button" data-position-analytics="${escapeHtml(pos.secid)}">${escapeHtml(pos.secid)}</button></td>
                    <td data-label="Количество, шт.">${pos.quantity}</td>
                    <td data-label="Средняя цена покупки">
                        <div>${Number(pos.avg_buy_price).toFixed(2)} ₽</div>
                        <div class="table-caption">Средняя цена покупки</div>
                    </td>
                    <td data-label="Текущая цена" class="${pos.is_price_missing ? 'negative' : ''}">${escapeHtml(pos.price_label || `${Number(pos.current_price).toFixed(2)} ₽`)}</td>
                    <td data-label="Стоимость позиции">${formatRubles(pos.market_value)}</td>
                    <td data-label="Изменение, %" class="${pos.profit_loss >= 0 ? 'positive' : 'negative'}">
                        ${pos.profit_loss_pct >= 0 ? '+' : ''}${Number(pos.profit_loss_pct).toFixed(2)}%
                    </td>
                    <td data-label="Прибыль / убыток, ₽" class="${pos.profit_loss >= 0 ? 'positive' : 'negative'}">
                        ${pos.profit_loss >= 0 ? '+' : ''}${formatRubles(pos.profit_loss)}
                    </td>
                    <td data-label="Действие" class="table-actions-cell">
                        <button type="button" class="btn btn-sell btn-sm" data-sell="${escapeHtml(pos.secid)}" data-price="${pos.current_price}" data-qty="${pos.quantity}" data-lot="${pos.lot_size}">
                            Продать
                        </button>
                    </td>
                </tr>
                <tr class="position-detail-row" id="portfolio-detail-row-${escapeHtml(pos.secid)}" hidden>
                    <td colspan="8">
                        <section class="chart-panel chart-panel--inline">
                            <div class="chart-panel-head">
                                <div>
                                    <h3 class="chart-panel-title" id="portfolio-chart-title-${escapeHtml(pos.secid)}">${escapeHtml(formatSecurityLabel(pos.secid, pos.shortname))}: история цены</h3>
                                    <p class="chart-panel-subtitle" id="portfolio-chart-subtitle-${escapeHtml(pos.secid)}">История бумаги и прогноз позиции.</p>
                                </div>
                            </div>
                            <div class="chart-meta" id="portfolio-chart-meta-${escapeHtml(pos.secid)}"></div>
                            <div class="chart-layout chart-layout--stack">
                                <div class="chart-canvas-wrap">
                                    <svg id="portfolio-chart-svg-${escapeHtml(pos.secid)}" class="line-chart" viewBox="0 0 640 320" preserveAspectRatio="xMidYMid meet"></svg>
                                </div>
                                <div class="forecast-grid" id="portfolio-position-forecast-grid-${escapeHtml(pos.secid)}"></div>
                            </div>
                        </section>
                    </td>
                </tr>`
                    )
                    .join('');

                // Обработчики кнопок "Продать"
                positionsBody.querySelectorAll('[data-sell]').forEach((btn) => {
                    btn.addEventListener('click', () => {
                        sellStock(
                            btn.dataset.sell,
                            parseFloat(btn.dataset.price),
                            parseFloat(btn.dataset.qty),
                            parseInt(btn.dataset.lot, 10),
                        );
                    });
                });
                // Обработчики кнопок аналитики позиций
                positionsBody.querySelectorAll('[data-position-analytics]').forEach((btn) => {
                    btn.addEventListener('click', () => loadSecurityAnalytics(btn.dataset.positionAnalytics, 'portfolio'));
                });
            }
        }

        // Рендер карточек прогноза портфеля
        renderForecastCards('portfolio-forecast-grid', portfolio.forecast, {
            subtitles: {
                month: 'Прогноз через месяц',
                year: 'Прогноз через год',
                ten_years: 'Прогноз через 10 лет',
            },
        });

        await loadPortfolioHistory(options);
    } 
    catch (error) {
        console.error('Ошибка загрузки портфеля:', error);
        showToast(error.message || 'Ошибка загрузки портфеля', 'error');
    }
}

// Загрузка и отрисовка истории портфеля (гистограмма)
async function loadPortfolioHistory(options = {}) {
    try {
        const query = options.forceRefresh ? '?refresh=1' : '';
        const data = await requestTrainerApi(`/api/portfolio/history${query}`);
        const historyMeta = document.getElementById('portfolio-history-meta');

        if (historyMeta) {
            historyMeta.dataset.prefix = data.history_started_at
                ? `История с ${formatFullDate(data.history_started_at)}`
                : '';
        }
        renderPortfolioHistoryChart('portfolio-history-svg', data.history, {
            metaId: 'portfolio-history-meta',
        });

        const diversificationStatus = document.getElementById('diversification-status');
        if (diversificationStatus && data.message) {
            diversificationStatus.textContent = `${data.message} Сейчас в портфеле: ${data.assets_count} компаний.`;
            diversificationStatus.classList.remove('positive', 'negative', 'muted');
            diversificationStatus.classList.add(data.assets_rule_ok ? 'positive' : 'negative');
        }
    } 
    catch (error) {
        const svg = document.getElementById('portfolio-history-svg');
        renderChartUnavailableState(svg, 'Не удалось построить историю портфеля');
    }
}

// Обновление баланса пользователя с обработкой ошибок
async function updateBalance(options = {}) {
    const balanceEl = document.getElementById('user-balance');
    if (!balanceEl) return;

    try {
        const portfolio = await getPortfolioSnapshot({ forceRefresh: !!options.forceRefresh });
        setBalanceSummary(portfolio.cash, portfolio.total_value);
    } 
    catch (e) {
        balanceEl.textContent = '0,00 ₽';
        const sub = document.getElementById('user-balance-sub');
        if (sub) {
            sub.textContent = 'Баланс временно недоступен';
        }
    }
}

// Покупка акций: модальное окно → валидация → отправка на сервер
async function buyStock(secid, price, lotSize) {
    if (tradeRequestInFlight) {
        showToast('Дождитесь завершения предыдущей операции', 'info');
        return;
    }

    const result = await openModal({
        title: `Покупка ${secid}`,
        bodyHtml: `
            <label class="field-label" for="buy-lots">Количество лотов (1 лот = ${lotSize} шт.)</label>
            <input type="number" id="buy-lots" class="field-input" min="1" step="1" value="1" />
            <p class="field-hint">Ориентир по карточке: ${Number(price).toFixed(2)} ₽ за акцию. Финальная цена фиксируется сервером по данным MOEX.</p>
        `,
        confirmText: 'Купить',
        cancelText: 'Отмена',
    });
    if (!result.ok) return;

    const lots = result.lots;
    if (!lots || lots < 1) {
        showToast('Укажите корректное число лотов', 'error');
        return;
    }

    const quantity = lots * lotSize;
    tradeRequestInFlight = true;

    try {
        await submitPortfolioTrade('/api/buy', {
            secid,
            quantity,
            successMessage: (result) =>
                `Куплено ${secid}: ${quantity} шт. по ${Number(result.executed_price || 0).toFixed(2)} ₽`,
        });
    } 
    catch (error) {
        showToast(error.message || 'Сеть или сервер недоступны', 'error');
    } 
    finally {
        tradeRequestInFlight = false;
    }
}

// Продажа акций: расчёт максимального количества лотов → модальное окно → отправка на сервер
async function sellStock(secid, price, maxQty, lotSize) {
    if (tradeRequestInFlight) {
        showToast('Дождитесь завершения предыдущей операции', 'info');
        return;
    }

    const maxInt = Math.floor(Number(maxQty));
    const maxLots = Math.floor(maxInt / lotSize);
    if (maxLots < 1) {
        showToast('Недостаточно бумаг для продажи целого лота', 'error');
        return;
    }
    const defaultLots = 1;
    const result = await openModal({
        title: `Продажа ${secid}`,
        bodyHtml: `
            <label class="field-label" for="sell-qty">Количество лотов (макс. ${maxLots})</label>
            <input type="number" id="sell-qty" class="field-input" min="1" max="${maxLots}" step="1" value="${defaultLots}" />
            <p class="field-hint">В одном лоте ${lotSize} шт. Финальная цена фиксируется сервером по данным MOEX.</p>
        `,
        confirmText: 'Продать',
        cancelText: 'Отмена',
    });
    if (!result.ok) return;

    const lots = result.qty;
    if (!lots || lots < 1 || lots > maxLots) {
        showToast('Некорректное количество', 'error');
        return;
    }
    const quantity = lots * lotSize;
    tradeRequestInFlight = true;

    try {
        await submitPortfolioTrade('/api/sell', {
            secid,
            quantity,
            successMessage: (result) =>
                `Продано ${secid}: ${quantity} шт. по ${Number(result.executed_price || 0).toFixed(2)} ₽`,
        });
    } 
    catch (error) {
        showToast(error.message || 'Сеть или сервер недоступны', 'error');
    } 
    finally {
        tradeRequestInFlight = false;
    }
}

// Применение стресс-сценария: расчёт, рендер таблицы результатов, подсветка худшей/лучшей бумаги
async function applyStress(scenario, buttonEl) {
    const resultDiv = document.getElementById('stress-result');
    const contentDiv = document.getElementById('stress-content');

    if (buttonEl) {
        buttonEl.disabled = true;
        buttonEl.classList.add('btn-loading');
    }
    try {
        const result = await requestTrainerApi('/api/stress', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ scenario }),
        });

        if (resultDiv) resultDiv.style.display = 'block';
        if (contentDiv) {
            const stressPositions = Array.isArray(result.positions) ? result.positions : [];
            let worstSecid = null;
            let bestSecid = null;
            
            // Поиск бумаг с максимальным падением и ростом для подсветки
            if (stressPositions.length) {
                const sortedByChange = [...stressPositions].sort((a, b) => Number(a.change_pct || 0) - Number(b.change_pct || 0));
                const worstCandidate = sortedByChange[0];
                const bestCandidate = sortedByChange[sortedByChange.length - 1];
                if (Number(worstCandidate?.change_pct || 0) < 0) {
                    worstSecid = worstCandidate.secid;
                }
                if (Number(bestCandidate?.change_pct || 0) > 0) {
                    bestSecid = bestCandidate.secid;
                }
            }
            
            // Генерация строк таблицы с подсветкой экстремумов
            const rows =
                stressPositions.length
                    ? stressPositions
                          .map((p) => {
                              const isWorst = worstSecid && p.secid === worstSecid;
                              const isBest = bestSecid && p.secid === bestSecid;
                              const emphasisClass = isWorst ? 'ticker-button--worst' : (isBest ? 'ticker-button--best' : '');
                              const stressHint = isWorst
                                  ? 'Эта акция упала сильнее всего в выбранном стресс-сценарии.'
                                  : (isBest ? 'Эта акция выросла сильнее всего в выбранном стресс-сценарии.' : '');
                              const directionClass = Number(p.change_pct || 0) >= 0 ? 'stress-row--up' : 'stress-row--down';
                              return `
                <tr class="${directionClass} ${isWorst ? 'stress-row--worst' : (isBest ? 'stress-row--best' : '')}">
                    <td><button type="button" class="ticker-button ${emphasisClass}" data-stress-analytics="${escapeHtml(p.secid)}" title="${escapeHtml(stressHint || formatSecurityLabel(p.secid, p.shortname))}"><strong>${escapeHtml(p.secid)}</strong></button></td>
                    <td>${formatRubles(p.current_price)}</td>
                    <td>${formatRubles(p.stress_price)}</td>
                    <td>${formatRubles(p.original_value)}</td>
                    <td>${formatRubles(p.stress_value)}</td>
                    <td class="${p.change_pct >= 0 ? 'positive' : 'negative'}">${p.change_pct >= 0 ? '+' : ''}${Number(p.change_pct).toFixed(1)}%</td>
                </tr>
                <tr>
                    <td colspan="6" class="muted" style="font-size:0.82rem;">${escapeHtml(p.explanation || '')}</td>
                </tr>
                <tr class="stress-detail-row" id="stress-detail-row-${escapeHtml(p.secid)}" hidden>
                    <td colspan="6">
                        <section class="chart-panel chart-panel--inline">
                            <div class="chart-panel-head">
                                <div>
                                    <h3 class="chart-panel-title" id="stress-chart-title-${escapeHtml(p.secid)}">${escapeHtml(formatSecurityLabel(p.secid, p.shortname))}: график в период сценария</h3>
                                    <p class="chart-panel-subtitle" id="stress-chart-subtitle-${escapeHtml(p.secid)}">Наведите на точки, чтобы увидеть дату и цену.</p>
                                </div>
                            </div>
                            <div class="chart-meta" id="stress-chart-meta-${escapeHtml(p.secid)}"></div>
                            <div class="chart-layout chart-layout--stack">
                                <div class="chart-canvas-wrap">
                                    <svg id="stress-chart-svg-${escapeHtml(p.secid)}" class="line-chart" viewBox="0 0 640 320" preserveAspectRatio="xMidYMid meet"></svg>
                                </div>
                                <div class="forecast-grid" id="stress-explanation-grid-${escapeHtml(p.secid)}"></div>
                            </div>
                        </section>
                    </td>
                </tr>`;
                          })
                          .join('')
                    : '<tr><td colspan="6">Нет позиций в портфеле для расчёта</td></tr>';

            // Сборка HTML результата стресс-теста
            contentDiv.innerHTML = `
                <div class="stress-head">
                    <h3>${escapeHtml(result.scenario_name || '')}</h3>
                    <p class="stress-desc">${escapeHtml(result.description || '')}</p>
                </div>
                <div class="stress-kpis">
                    <div class="kpi">
                        <span class="kpi-label">До сценария</span>
                        <span class="kpi-value">${formatRubles(result.original_value)}</span>
                    </div>
                    <div class="kpi">
                        <span class="kpi-label">После</span>
                        <span class="kpi-value">${formatRubles(result.stress_value)}</span>
                    </div>
                    <div class="kpi kpi--wide">
                        <span class="kpi-label">Изменение</span>
                        <span class="kpi-value ${result.total_change >= 0 ? 'positive' : 'negative'}">
                            ${result.total_change_pct >= 0 ? '+' : ''}${Number(result.total_change_pct).toFixed(2)}% (${formatRubles(result.total_change)})
                        </span>
                    </div>
                </div>
                <div class="stress-table-wrap">
                    <table class="stress-table">
                        <thead>
                            <tr>
                                <th>Тикер</th>
                                <th>Цена до</th>
                                <th>Цена после</th>
                                <th>Стоимость до</th>
                                <th>Стоимость после</th>
                                <th>Изм., %</th>
                            </tr>
                        </thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>
            `;
            
            // Обработчики для кнопок аналитики в таблице
            contentDiv.querySelectorAll('[data-stress-analytics]').forEach((btn) => {
                btn.addEventListener('click', () => loadStressSecurityHistory(result.scenario_slug, btn.dataset.stressAnalytics));
            });
            
            // Автозагрузка графика для худшей/лучшей бумаги при первом показе
            const initialStressSecid = worstSecid || bestSecid || stressPositions[0]?.secid;
            if (initialStressSecid) {
                loadStressSecurityHistory(result.scenario_slug, initialStressSecid);
            }
        }
        if (resultDiv) resultDiv.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } 
    catch (error) {
        showToast(error.message || 'Ошибка стресс-теста', 'error');
    } 
    finally {
        if (buttonEl) {
            buttonEl.disabled = false;
            buttonEl.classList.remove('btn-loading');
        }
    }
}

// Загрузка статистики пользователя с обновлением индикаторов
async function loadStats(options = {}) {
    const sharpeEl = document.getElementById('sharpe-ratio');

    if (!sharpeEl) return;

    try {
        const query = options.forceRefresh ? '?refresh=1' : '';
        const stats = await requestTrainerApi(`/api/stats${query}`);
        const sharpeValue = Number(stats.sharpe_ratio || 0);

        applySharpeState(sharpeValue);

        const roiEl = document.getElementById('stat-roi');
        if (roiEl) {
            roiEl.classList.remove('stat-value--positive', 'stat-value--negative');
            roiEl.classList.add(Number(stats.total_profit_pct) >= 0 ? 'stat-value--positive' : 'stat-value--negative');
        }
    } catch (error) {
        console.error('Ошибка загрузки статистики:', error);
    }
}

// Принудительное обновление портфеля с уведомлением
function refreshPortfolio() {
    loadPortfolio({ forceRefresh: true });
    showToast('Запрошены свежие данные MOEX для портфеля и акций', 'info');
}

// Инициализация по DOMContentLoaded: загрузка контента в зависимости от текущей страницы
document.addEventListener('DOMContentLoaded', () => {
    const path = window.location.pathname;

    // Главная страница: список бумаг
    if (path === '/' || path === '') {
        loadStocks({ forceSelect: true });
        loadMarketNews(MARKET_NEWS_QUERY);
        updateGlobalTradeStats();
        const search = document.getElementById('stock-search');

        if (search) {
            let t;
            // Дебаунс ввода для фильтрации (200мс задержка)
            search.addEventListener('input', () => {
                clearTimeout(t);
                t = setTimeout(() => filterStocks(search.value), 200);
            });
        }

        const refreshBtn = document.getElementById('refresh-quotes');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', () => {
                refreshBtn.classList.add('btn-loading');
                loadStocks({ forceRefresh: true, forceSelect: true })
                    .then(() => showToast('Котировки и параметры бумаг обновлены с MOEX', 'success'))
                    .finally(() => refreshBtn.classList.remove('btn-loading'));
            });
        }

        // Автообновление котировок каждые 5 минут
        setInterval(() => loadStocks(), 300000);
        setInterval(() => loadMarketNews(MARKET_NEWS_QUERY), 300000);
    }

    // Страница портфеля
    if (path === '/portfolio') {
        loadPortfolio({ forceRefresh: true });
        setInterval(() => loadPortfolio({ forceRefresh: true }), 300000);
    }

    // Страница стресс-тестов
    if (path === '/stress') {
        document.querySelectorAll('.btn-apply').forEach((btn) => {
            btn.addEventListener('click', () => {
                const card = btn.closest('.scenario-card');
                const slug = card && card.dataset ? card.dataset.slug : null;
                if (slug) applyStress(slug, btn);
            });
        });
    }
});

// Экспорт функций в глобальную область для использования из HTML-обработчиков
window.refreshPortfolio = refreshPortfolio;
window.applyStress = applyStress;
