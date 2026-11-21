"""
Mandarake 扩展钩子模块
"""
from hooks.auto_order import (
    auto_order_engine,
    MandarakeAutoOrder,
    INSTANT_PAYMENT_METHODS,
    EXCLUDED_PAYMENT_METHODS,
    JP_PREFECTURES,
    DEFAULT_PURCHASE_CONFIG,
)

__all__ = [
    "auto_order_engine",
    "MandarakeAutoOrder",
    "INSTANT_PAYMENT_METHODS",
    "EXCLUDED_PAYMENT_METHODS",
    "JP_PREFECTURES",
    "DEFAULT_PURCHASE_CONFIG",
]
