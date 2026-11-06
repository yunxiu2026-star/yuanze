"""Atomic, authenticated password verifier; no business DB or plaintext writes."""
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time

ITERATIONS=600_000

class CredentialStoreError(RuntimeError):pass
class OldPasswordError(ValueError):pass
class PasswordPolicyError(ValueError):pass

def validate_password(value):
    if not value or len(value)>128:
        raise PasswordPolicyError('新密码不能为空，且最多 128 个字符。')
    if any(ord(c)<32 or ord(c)==127 or 0xd800<=ord(c)<=0xdfff for c in value):
        raise PasswordPolicyError('新密码不能包含换行或控制字符。')
    if not any(c.isalpha() for c in value) or not any(c.isdigit() for c in value):
        raise PasswordPolicyError('新密码须同时包含字母和数字，可加入符号或空格。')

class PasswordStore:
    def __init__(self,folder,secret,bootstrap_digest):
        self.folder=Path(folder);self.path=self.folder/'password.json';self.marker=self.folder/'initialized'
        self.secret=secret;self.bootstrap_digest=bootstrap_digest;self.thread_lock=threading.RLock()

    def mac(self,value):
        data=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
        return hmac.new(self.secret,b'geo-password-store-v1|'+data,hashlib.sha256).hexdigest()

    def read(self):
        try:
            if self.folder.is_symlink() or self.path.is_symlink() or self.marker.is_symlink():raise ValueError()
            try:
                with self.path.open('rb') as stream:raw=stream.read(4097)
            except FileNotFoundError:
                try:self.marker.stat()
                except FileNotFoundError:return None
                raise ValueError()
            if len(raw)>4096:raise ValueError()
            record=json.loads(raw);signature=record.pop('mac')
            if not isinstance(signature,str) or not hmac.compare_digest(signature,self.mac(record)):raise ValueError()
            if set(record)!={'version','algorithm','iterations','salt','digest','generation','changed_at'}:raise ValueError()
            if record['version']!=1 or record['algorithm']!='pbkdf2_sha256' or type(record['iterations']) is not int or not 600_000<=record['iterations']<=2_000_000:raise ValueError()
            for field,size in [('salt',32),('digest',64),('generation',64)]:
                if not isinstance(record[field],str) or not re.fullmatch('[0-9a-f]{'+str(size)+'}',record[field]):raise ValueError()
            if not datetime.fromisoformat(record['changed_at']).tzinfo:raise ValueError()
            return record
        except (OSError,ValueError,TypeError,KeyError,AttributeError):
            raise CredentialStoreError('密码配置暂不可读取，请联系管理员核对持久化目录；没有回退到旧密码。') from None

    def matches(self,password,record):
        if len(password)>1024:return False
        if record is None:return hmac.compare_digest(hashlib.sha256(password.encode()).digest(),self.bootstrap_digest)
        digest=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(record['salt']),record['iterations']).hex()
        return hmac.compare_digest(digest,record['digest'])

    @contextmanager
    def exclusive(self):
        try:
            if self.folder.is_symlink():raise OSError()
            self.folder.mkdir(parents=True,exist_ok=True,mode=0o700)
            if os.name!='nt':os.chmod(self.folder,0o700)
            lockpath=self.folder/'password.lock'
            if lockpath.is_symlink():raise OSError()
            flags=os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0)
            descriptor=os.open(lockpath,flags,0o600)
        except OSError:raise CredentialStoreError('无法写入密码配置目录，密码未更新。') from None
        with self.thread_lock,os.fdopen(descriptor,'r+b') as lock:
            if os.fstat(lock.fileno()).st_size==0:lock.write(b'0');lock.flush()
            acquired=False;deadline=time.monotonic()+8
            try:
                while not acquired:
                    try:
                        if os.name=='nt':
                            import msvcrt
                            lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
                        else:
                            import fcntl
                            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                        acquired=True
                    except OSError:
                        if time.monotonic()>deadline:raise CredentialStoreError('其他设备正在修改密码，请稍后重试。') from None
                        time.sleep(.03)
                yield
            finally:
                if acquired:
                    if os.name=='nt':
                        lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
                    else:fcntl.flock(lock,fcntl.LOCK_UN)

    def rotate(self,old_password,new_password):
        validate_password(new_password)
        with self.exclusive():
            record=self.read()
            if not self.matches(old_password,record):raise OldPasswordError('旧密码不正确，密码未更新。')
            if self.matches(new_password,record):raise PasswordPolicyError('新密码须与当前密码不同。')
            salt=secrets.token_bytes(16)
            result={'version':1,'algorithm':'pbkdf2_sha256','iterations':ITERATIONS,'salt':salt.hex(),
                    'digest':hashlib.pbkdf2_hmac('sha256',new_password.encode(),salt,ITERATIONS).hex(),
                    'generation':secrets.token_hex(32),'changed_at':datetime.now(timezone.utc).isoformat()}
            payload={**result,'mac':self.mac(result)}
            temp=self.folder/('password-'+secrets.token_hex(12)+'.tmp');new_marker=False
            try:
                descriptor=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(descriptor,'w',encoding='utf-8') as file:
                    json.dump(payload,file,sort_keys=True);file.flush();os.fsync(file.fileno())
                if not self.marker.exists():
                    descriptor=os.open(self.marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                    new_marker=True
                    with os.fdopen(descriptor,'wb') as file:file.write(b'geo-password-store-v1\n');file.flush();os.fsync(file.fileno())
                os.replace(temp,self.path)
            except OSError:
                if temp.exists():temp.unlink()
                if new_marker and not self.path.exists():self.marker.unlink()
                raise CredentialStoreError('无法保存密码配置，密码未更新，请稍后重试。') from None
            if os.name!='nt':
                try:
                    descriptor=os.open(self.folder,os.O_RDONLY|os.O_DIRECTORY)
                    try:os.fsync(descriptor)
                    finally:os.close(descriptor)
                except OSError:pass
            return result
