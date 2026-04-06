#!/usr/bin/env python3
"""Диагностика YNDX на MOEX."""
import requests
import json

tickers_to_check = ["YNDX", "YDEX", "YNDX.ME"]
boards_to_check = ["TQBR", "TQTF", "EQPR", "SPBX"]

print("=" * 70)
print("ДИАГНОСТИКА ЯНДЕКС НА МОСБИРЖЕ")
print("=" * 70)

for ticker in tickers_to_check:
    for board in boards_to_check:
        url = f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/{board}/securities/{ticker}.json"
        params = {"iss.meta": "off", "iss.only": "marketdata,securities"}
        
        try:
            r = requests.get(url, params=params, timeout=10, proxies={'http': None, 'https': None})
            if r.status_code == 200:
                data = r.json()
                md = data.get("marketdata", {})
                rows = md.get("data", [])
                cols = md.get("columns", [])
                
                price = None
                if rows and cols:
                    for field in ["LAST", "LCURRENTPRICE", "CURRENTPRICE", "CLOSE", "PREVPRICE"]:
                        if field in cols:
                            idx = cols.index(field)
                            if len(rows[0]) > idx and rows[0][idx] not in (None, "", "null"):
                                try:
                                    price = float(rows[0][idx])
                                    break
                                except:
                                    pass
                
                if price or rows:
                    print(f"✅ {ticker:8s} @ {board:6s} → Цена: {price if price else 'N/A':>8} | Rows: {len(rows)}")
                    if price and 1000 < price < 10000:  # Реалистичный диапазон для Яндекса
                        print(f"   🎯 ПОДХОДИТ! Используйте тикер={ticker}, board={board}")
        except Exception as e:
            pass  # Игнорируем ошибки

print("=" * 70)
print("Если ничего не найдено — Яндекс может быть недоступен на Мосбирже.")
print("В этом случае используйте FALLBACK-цену или альтернативный источник.")
print("=" * 70)