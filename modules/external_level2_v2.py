# -*- coding: utf-8 -*-
"""Reliability layer for modules.external_level2.

Subscriptions are desired-state, not one-shot calls. The recorder may start
before txtool.exe or before the provider account is configured; this layer keeps
retrying in a background thread until the local proxy accepts the subscription.
No market-data/label loop is blocked by gRPC AddSubscription timeouts.
"""
from __future__ import annotations

import threading
import time
from typing import Tuple

import modules.external_level2 as base


def _desired(self):
    with self.lock:
        if not hasattr(self, "desired_symbols"):
            self.desired_symbols = set()
        return self.desired_symbols


def _try_subscription(self, symbol: str) -> Tuple[bool, str]:
    symbol = str(symbol).upper().strip()
    market = base._market_code(symbol)
    if not symbol or not market:
        return False, f"unsupported symbol {symbol}"
    code = symbol.split(".")[0]
    topic = f"{market}_{code}_{int(self.cfg.get('topic_mask') or 15)}"
    if not bool(self.cfg.get("auto_subscribe", True)):
        with self.lock:
            self.subscribed[symbol] = topic
        return True, topic
    if not self.runtime_available:
        return False, self.last_error or "external client unavailable"
    try:
        req = self.entity.String(value=topic)
        result = self.stub.AddSubscription(req, timeout=2.0)
        code_value = int(getattr(result, "code", 0) or 0)
        if code_value not in (0, 1):
            return False, f"AddSubscription code={code_value} result={result}"
        with self.lock:
            self.subscribed[symbol] = topic
        return True, topic
    except Exception as exc:
        self.last_error = f"AddSubscription: {exc}"
        return False, self.last_error


def _subscription_loop(self) -> None:
    while not self.stop_event.is_set():
        try:
            if not self.runtime_available:
                self._load_client()
            with self.lock:
                desired = list(_desired(self))
                done = set(self.subscribed)
            for symbol in desired:
                if symbol in done:
                    continue
                _try_subscription(self, symbol)
                if self.stop_event.is_set():
                    return
        except Exception as exc:
            self.last_error = f"subscription_loop: {exc}"
        time.sleep(2.0)


def _start(self) -> None:
    with self.lock:
        if self.started:
            return
        self.started = True
    # Start workers even if txtool/proto is not ready yet. Receiver/subscription
    # loops retry client loading, so later proxy startup needs no recorder restart.
    streams = (
        ("NewTickRecordStream", self._on_tick),
        ("NewOrderRecordStream", self._on_order),
        ("NewOrderQueueRecordStream", self._on_queue),
        ("NewStockQuoteRecordStream", self._on_quote),
    )
    for method, handler in streams:
        threading.Thread(
            target=self._receiver,
            args=(method, handler),
            daemon=True,
            name=f"astock-level2api-{method}",
        ).start()
    threading.Thread(
        target=_subscription_loop,
        args=(self,),
        daemon=True,
        name="astock-level2api-subscriptions",
    ).start()


def _ensure_subscription(self, symbol: str) -> Tuple[bool, str]:
    symbol = str(symbol).upper().strip()
    if not symbol:
        return False, "empty symbol"
    with self.lock:
        _desired(self).add(symbol)
        existing = self.subscribed.get(symbol)
    self.start()
    if existing:
        return True, existing
    market = base._market_code(symbol)
    code = symbol.split(".")[0]
    topic = f"{market}_{code}_{int(self.cfg.get('topic_mask') or 15)}" if market else symbol
    # Pending is an expected state while txtool/account is not ready. The
    # background loop will promote it to subscribed automatically.
    return True, f"pending:{topic}"


# Patch the shared hub class before the singleton is constructed.
base._Hub.start = _start
base._Hub.ensure_subscription = _ensure_subscription

ExternalLevel2Manager = base.ExternalLevel2Manager
external_level2_enabled = base.external_level2_enabled
provider_name = base.provider_name
