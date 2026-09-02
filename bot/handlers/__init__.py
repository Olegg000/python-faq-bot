"""Сборка общего роутера из всех модулей пакета handlers."""

import importlib
import pkgutil

from aiogram import Router

router = Router()

for module_info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
    module = importlib.import_module(f"{__name__}.{module_info.name}")
    if isinstance(getattr(module, "router", None), Router):
        router.include_router(module.router)
