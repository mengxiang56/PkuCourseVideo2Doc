"""Credentials owned by this tool, stored in the current user's Windows vault."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path

SITES = {"pku": "北大统一身份认证", "tingwu": "通义听悟"}


@dataclass(repr=False, frozen=True)
class SavedCredential:
    username: str
    password: str

    def __repr__(self):
        return "<SavedCredential>"


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(wintypes.BYTE)),
        ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class CredentialStore:
    def __init__(self, root: Path, *, namespace: str | None = None):
        digest = hashlib.sha256(str(root.resolve()).casefold().encode()).hexdigest()[:16]
        self.namespace = namespace or f"PKUVideo2Doc/{digest}"

    def _target(self, site):
        if site not in SITES:
            raise ValueError("未知的账号类型。")
        return self.namespace + "/" + site

    @staticmethod
    def _api():
        if os.name != "nt":
            raise RuntimeError("保存密码需要 Windows 凭据管理器。")
        api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        api.CredWriteW.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
        api.CredWriteW.restype = wintypes.BOOL
        api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.POINTER(ctypes.POINTER(_Credential))]
        api.CredReadW.restype = wintypes.BOOL
        api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        api.CredDeleteW.restype = wintypes.BOOL
        api.CredFree.argtypes = [ctypes.c_void_p]
        api.CredFree.restype = None
        return api

    def save(self, site: str, username: str, password: str):
        target = self._target(site)
        username = username.strip()
        if not username or not password or "\x00" in username:
            raise ValueError("请填写账号和密码。")
        encoded = password.encode("utf-8")
        if len(encoded) > 2560:
            raise ValueError("密码长度超过 Windows 凭据管理器的限制。")
        api = self._api()
        buffer = ctypes.create_string_buffer(encoded)
        credential = _Credential()
        credential.Type = 1  # CRED_TYPE_GENERIC; the application defines the blob.
        credential.TargetName = target
        credential.Comment = "课程文字稿：" + SITES[site]
        credential.CredentialBlobSize = len(encoded)
        credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(wintypes.BYTE))
        credential.Persist = 2  # Same Windows user on this computer across logons.
        credential.UserName = username
        try:
            if not api.CredWriteW(ctypes.byref(credential), 0):
                raise RuntimeError(f"Windows 凭据保存失败（错误码 {ctypes.get_last_error()}）。")
        finally:
            ctypes.memset(buffer, 0, ctypes.sizeof(buffer))

    def _read(self, site, *, include_password):
        target = self._target(site)
        api = self._api()
        pointer = ctypes.POINTER(_Credential)()
        if not api.CredReadW(target, 1, 0, ctypes.byref(pointer)):
            code = ctypes.get_last_error()
            if code == 1168:  # ERROR_NOT_FOUND
                return None
            raise RuntimeError(f"Windows 凭据读取失败（错误码 {code}）。")
        try:
            stored = pointer.contents
            if include_password:
                password = ctypes.string_at(stored.CredentialBlob, stored.CredentialBlobSize).decode("utf-8")
                return SavedCredential(stored.UserName or "", password)
            return stored.UserName or ""
        finally:
            if pointer.contents.CredentialBlob and pointer.contents.CredentialBlobSize:
                ctypes.memset(pointer.contents.CredentialBlob, 0, pointer.contents.CredentialBlobSize)
            api.CredFree(pointer)

    def load(self, site: str) -> SavedCredential | None:
        return self._read(site, include_password=True)

    def username(self, site: str) -> str | None:
        return self._read(site, include_password=False)

    def delete(self, site: str):
        target = self._target(site)
        api = self._api()
        if not api.CredDeleteW(target, 1, 0):
            code = ctypes.get_last_error()
            if code != 1168:
                raise RuntimeError(f"Windows 凭据删除失败（错误码 {code}）。")
