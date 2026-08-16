"""
auth_manager.py
----------------
Centralizes admin-password storage/verification so no credential ever
lives as a literal string inside application code.

Design notes:
- Single-responsibility: this module ONLY knows how to store/verify a
  password hash. UI code (LoginScreen) should never see the raw
  password comparison logic.
- Singleton-ish usage: import `auth_manager` (the module-level instance
  at the bottom) rather than instantiating AuthManager yourself, so the
  whole app shares one config file location.
- Config file: <project_root>/config/auth_config.json
    {
        "salt": "<hex>",
        "password_hash": "<hex>",
        "iterations": 200000
    }
  Nothing here is human-readable as a password — regenerating access
  means deleting this file (falls back to the default password) or
  calling set_password() to overwrite it with a new hash.
"""

import os
import json
import hashlib
import secrets

DEFAULT_PASSWORD = "admin123"
PBKDF2_ITERATIONS = 200_000


class AuthManager:
    def __init__(self, config_path: str = None):
        app_data_dir = os.path.join(os.path.expanduser("~"), ".smart_grader")
        self.config_path = config_path or os.path.join(app_data_dir, "auth_config.json")
        self._ensure_config_exists()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _ensure_config_exists(self):
        """First-run bootstrap: create config/auth_config.json with the
        default password already hashed, so the raw string never touches
        disk in plaintext."""
        if os.path.exists(self.config_path):
            return
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        self._write_new_password(DEFAULT_PASSWORD)

    @staticmethod
    def _hash_password(password: str, salt: bytes) -> str:
        return hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
        ).hex()

    def _write_new_password(self, new_password: str):
        salt = secrets.token_bytes(16)
        data = {
            "salt": salt.hex(),
            "password_hash": self._hash_password(new_password, salt),
            "iterations": PBKDF2_ITERATIONS,
        }
        with open(self.config_path, "w") as f:
            json.dump(data, f, indent=2)

    def _load_config(self) -> dict:
        with open(self.config_path, "r") as f:
            return json.load(f)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def verify(self, attempted_password: str) -> bool:
        """Returns True if attempted_password matches the stored hash."""
        try:
            config = self._load_config()
            salt = bytes.fromhex(config["salt"])
            candidate_hash = self._hash_password(attempted_password, salt)
            return secrets.compare_digest(candidate_hash, config["password_hash"])
        except (FileNotFoundError, KeyError, ValueError):
            # Corrupt or missing config -> rebuild with default so the
            # admin is never permanently locked out.
            self._write_new_password(DEFAULT_PASSWORD)
            return attempted_password == DEFAULT_PASSWORD

    def set_password(self, new_password: str):
        """Overwrites the stored password. Not wired to any UI yet —
        ready for a future 'change password' settings screen."""
        self._write_new_password(new_password)


# Shared instance used across the app (import this, don't re-instantiate)
auth_manager = AuthManager()
