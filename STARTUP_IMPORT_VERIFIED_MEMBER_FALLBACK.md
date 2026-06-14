Add this import in `main.py` right after `guild_config_runtime_validator`:

```py
import stoney_verify.startup_guards.verification_member_role_fallback_guard  # noqa: F401,E402
```

This wires the existing fallback guard so servers can use Verified as the effective Member/Resident role.
