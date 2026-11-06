"""Single-password login with a signed session cookie, CSRF tokens and a
simple per-IP lockout after repeated failures."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
from .password_store import PasswordStore,OldPasswordError

COOKIE = "geo_session"
MAX_AGE = 7 * 24 * 3600
MAX_FAILS = 5
LOCK_SECONDS = 15 * 60


class Auth:
    def __init__(self, password: str, secret: str, credential_dir=None):
        if not password or len(password) < 10:
            raise SystemExit("请在 .env 里设置 WEB_PASSWORD（至少 10 位）")
        if not secret or len(secret) < 32:
            raise SystemExit("请在 .env 里设置 WEB_SECRET（至少 32 位随机字符）")
        self._pw = hashlib.sha256(password.encode()).digest()
        self._secret = secret.encode()
        # Changing the password invalidates every existing session.
        self._bootstrap_signer = TimestampSigner(secret + "|" + hashlib.sha256(password.encode()).hexdigest()[:16],
                                      salt="geo-session")
        self.store=PasswordStore(credential_dir,self._secret,self._pw) if credential_dir is not None else None
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.RLock()

    @property
    def signer(self):
        record=self.store.read() if self.store else None
        if record is None:return self._bootstrap_signer
        return TimestampSigner(self._secret.decode()+'|password-version:'+record['generation'],salt='geo-session')

    def password_state(self):
        record=self.store.read() if self.store else None
        return {'changed_at':record['changed_at'] if record else ''}

    def change_password(self,ip,old_password,new_password):
        if not self.store:raise RuntimeError('Persistent credential directory is required')
        key='change:'+ip
        if self.locked(key):return False
        try:self.store.rotate(old_password,new_password)
        except OldPasswordError:
            with self._lock:self._fails.setdefault(key,[]).append(time.time())
            raise
        with self._lock:self._fails.pop(key,None)
        return True

    def login_session(self,ip,password):
        # Serialize credential verification + session creation with password rotation.
        if self.store:
            with self.store.exclusive():
                return self.new_session() if self.check_password(ip,password) else None
        return self.new_session() if self.check_password(ip,password) else None

    # --- password & lockout -------------------------------------------------
    def locked(self, ip: str) -> int:
        """Seconds remaining in a lockout for this IP (0 = not locked)."""
        with self._lock:
            now = time.time()
            fails = [t for t in self._fails.get(ip, []) if now - t < LOCK_SECONDS]
            self._fails[ip] = fails
            if len(fails) >= MAX_FAILS:
                return int(LOCK_SECONDS - (now - fails[0])) + 1
            return 0

    def check_password(self, ip: str, password: str) -> bool:
        ok = self.store.matches(password,self.store.read()) if self.store else hmac.compare_digest(hashlib.sha256(password.encode()).digest(), self._pw)
        with self._lock:
            if ok:
                self._fails.pop(ip, None)
            else:
                self._fails.setdefault(ip, []).append(time.time())
        return ok

    # --- session ------------------------------------------------------------
    def new_session(self) -> str:
        return self.signer.sign(secrets.token_urlsafe(16)).decode()

    def session_id(self, cookie: str | None) -> str | None:
        if not cookie:
            return None
        try:
            return self.signer.unsign(cookie, max_age=MAX_AGE).decode()
        except (BadSignature, SignatureExpired):
            return None

    def csrf_token(self, sid: str) -> str:
        return hmac.new(self._secret, b"csrf|" + sid.encode(), hashlib.sha256).hexdigest()

    def check_csrf(self, sid: str, token: str | None) -> bool:
        return bool(token) and hmac.compare_digest(self.csrf_token(sid), token)
