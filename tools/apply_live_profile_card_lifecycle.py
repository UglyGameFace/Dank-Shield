from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    source = target.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"expected one anchor in {path}: {old!r}; found {count}")
    target.write_text(source.replace(old, new, 1), encoding="utf-8")


replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''LIVE_CARD_HISTORY_SCAN_LIMIT = 100\nLIVE_CARD_FOOTER_PREFIX = "Dank Shield live profile"\n''',
    '''LIVE_CARD_HISTORY_SCAN_LIMIT = 100\nREADY_RECONCILE_THROTTLE_SECONDS = 60.0\nLIVE_CARD_FOOTER_PREFIX = "Dank Shield live profile"\n''',
)

replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''        self._locks: dict[tuple[int, int], asyncio.Lock] = {}\n        self._last_posted: dict[tuple[int, int], tuple[int, float]] = {}\n\n    async def on_message(self, message: discord.Message) -> None:\n''',
    '''        self._locks: dict[tuple[int, int], asyncio.Lock] = {}\n        self._last_posted: dict[tuple[int, int], tuple[int, float]] = {}\n        self._reconcile_lock = asyncio.Lock()\n        self._last_reconcile_at = 0.0\n\n    async def on_ready(self) -> None:\n        """Reconcile durable card ownership once per ready/reconnect window."""\n        now = monotonic()\n        if now - self._last_reconcile_at < READY_RECONCILE_THROTTLE_SECONDS:\n            return\n        async with self._reconcile_lock:\n            now = monotonic()\n            if now - self._last_reconcile_at < READY_RECONCILE_THROTTLE_SECONDS:\n                return\n            # Set before I/O so repeated ready events cannot create a retry storm\n            # when Discord or private storage is temporarily unavailable.\n            self._last_reconcile_at = now\n            try:\n                await self.reconcile()\n            except asyncio.CancelledError:\n                raise\n            except Exception as exc:\n                print(\n                    "⚠️ live_profile_card ready reconciliation failed safely: "\n                    f"{type(exc).__name__}: {exc}"\n                )\n\n    async def on_message(self, message: discord.Message) -> None:\n''',
)

replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''    async def reconcile_after_ready(self) -> None:\n        try:\n            await self.bot.wait_until_ready()\n            await self.reconcile()\n        except asyncio.CancelledError:\n            raise\n        except Exception as exc:\n            print(f"⚠️ live_profile_card reconcile failed safely: {type(exc).__name__}: {exc}")\n\n    async def reconcile(self) -> None:\n''',
    '''    async def reconcile(self) -> None:\n''',
)

replace_once(
    "stoney_verify/profile_card_runtime.py",
    '''    "LIVE_SAME_SPEAKER_COOLDOWN_KEY",\n    "LiveCardConfig",\n''',
    '''    "LIVE_SAME_SPEAKER_COOLDOWN_KEY",\n    "READY_RECONCILE_THROTTLE_SECONDS",\n    "LiveCardConfig",\n''',
)

replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    '''        setattr(bot, _RUNTIME_ATTRIBUTE, runtime)\n        bot.add_listener(runtime.on_message, "on_message")\n        reconcile_task = asyncio.create_task(runtime.reconcile_after_ready())\n        setattr(runtime, "_reconcile_task", reconcile_task)\n''',
    '''        setattr(bot, _RUNTIME_ATTRIBUTE, runtime)\n        bot.add_listener(runtime.on_message, "on_message")\n        bot.add_listener(runtime.on_ready, "on_ready")\n''',
)

print("Applied lifecycle-safe live profile reconciliation.")
