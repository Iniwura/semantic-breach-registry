"""
Studio Dev and Local Direct VM Harness for SemanticBreachRegistry.
Provides fixtures: direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import types
import re
import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional
import pytest


# ─────────────────────────────────────────────────────────────
# 1. GenLayer Native Types Simulation
# ─────────────────────────────────────────────────────────────

class Address(str):
    """GenLayer 20-byte account address simulation."""
    def __new__(cls, val):
        if isinstance(val, bytes):
            return super().__new__(cls, "0x" + val.hex())
        val_str = str(val)
        if not val_str.startswith("0x"):
            val_str = "0x" + val_str
        return super().__new__(cls, val_str)


class u256(int):
    """Unsigned 256-bit integer."""
    def __new__(cls, val=0):
        v = int(val)
        if v < 0 or v >= 2**256:
            raise ValueError(f"u256 overflow: {v}")
        return super().__new__(cls, v)


class u64(int):
    """Unsigned 64-bit integer."""
    def __new__(cls, val=0):
        v = int(val)
        if v < 0 or v >= 2**64:
            raise ValueError(f"u64 overflow: {v}")
        return super().__new__(cls, v)


class u32(int):
    """Unsigned 32-bit integer."""
    def __new__(cls, val=0):
        v = int(val)
        if v < 0 or v >= 2**32:
            raise ValueError(f"u32 overflow: {v}")
        return super().__new__(cls, v)


class TreeMap(dict):
    """Key-value storage mapping for GenVM."""
    pass


def allow_storage(cls):
    """Dataclass storage decorator for GenVM."""
    return cls


class UserError(Exception):
    """Revert exception within GenLayer contracts."""
    pass


class ConsensusDisagreementError(Exception):
    """Consensus validation failure when validators disagree on strict equivalence."""
    pass


# ─────────────────────────────────────────────────────────────
# 2. Virtual Machine Context & Mock Engine
# ─────────────────────────────────────────────────────────────

class VMContext:
    def __init__(self):
        self.sender_address = Address("0x1111111111111111111111111111111111111111")
        self.value = u256(0)
        self.web_mocks: Dict[str, Any] = {}
        self.llm_mocks: Dict[str, Any] = {}
        self.default_llm_response: Optional[Any] = None
        self.default_web_response: Optional[Any] = None
        self.simulate_disagreement: bool = False

    @property
    def sender(self) -> Address:
        return self.sender_address

    @sender.setter
    def sender(self, val: Any):
        self.sender_address = Address(val)

    def clear_mocks(self):
        self.web_mocks.clear()
        self.llm_mocks.clear()
        self.default_llm_response = None
        self.default_web_response = None
        self.simulate_disagreement = False

    def mock_web(self, pattern: str, response: Any):
        self.web_mocks[pattern] = response

    def mock_llm(self, pattern: str, response: Any):
        self.llm_mocks[pattern] = response

    def render_web(self, url: str, mode: str = "text") -> str:
        for pat, resp in self.web_mocks.items():
            if re.search(pat, url):
                if isinstance(resp, dict) and "body" in resp:
                    b = resp["body"]
                    return b.decode("utf-8") if isinstance(b, bytes) else str(b)
                return str(resp)
        if self.default_web_response is not None:
            return str(self.default_web_response)
        return ""

    def exec_prompt(self, prompt: str, response_format: str = "json") -> Any:
        for pat, resp in self.llm_mocks.items():
            if re.search(pat, prompt, re.DOTALL):
                return resp
        if self.default_llm_response is not None:
            return self.default_llm_response
        return {"severity": "NONE", "reason": "Default mock response"}

    def strict_eq(self, fn: Callable[[], Any]) -> Any:
        # Leader evaluation
        leader_result = fn()
        if self.simulate_disagreement:
            raise ConsensusDisagreementError("Validators disagree: equivalence check failed.")
        return leader_result


_CURRENT_VM = VMContext()


# ─────────────────────────────────────────────────────────────
# 3. Dynamic GenLayer Module Registration
# ─────────────────────────────────────────────────────────────

def _ensure_genlayer_module():
    if "genlayer" in sys.modules:
        return sys.modules["genlayer"]

    gl_mod = types.ModuleType("genlayer")

    class Contract:
        def __new__(cls, *args, **kwargs):
            instance = super().__new__(cls)
            for attr_name, attr_type in getattr(cls, "__annotations__", {}).items():
                if "TreeMap" in str(attr_type):
                    setattr(instance, attr_name, TreeMap())
            return instance

    class _WriteDecorator:
        def __call__(self, fn):
            return fn

        def payable(self, fn):
            return fn

    class PublicNamespace:
        write = _WriteDecorator()

        @staticmethod
        def view(fn):
            return fn

    class WebNamespace:
        @staticmethod
        def render(url: str, mode: str = "text") -> str:
            return _CURRENT_VM.render_web(url, mode)

    class NondetNamespace:
        web = WebNamespace()

        @staticmethod
        def exec_prompt(prompt: str, response_format: str = "json") -> Any:
            return _CURRENT_VM.exec_prompt(prompt, response_format)

    class EqPrincipleNamespace:
        @staticmethod
        def strict_eq(fn: Callable[[], Any]) -> Any:
            return _CURRENT_VM.strict_eq(fn)

    class MessageProxy:
        @property
        def sender_address(self):
            return _CURRENT_VM.sender_address

        @property
        def value(self):
            return _CURRENT_VM.value

    # Populate module
    gl_mod.Contract = Contract
    gl_mod.public = PublicNamespace()
    gl_mod.nondet = NondetNamespace()
    gl_mod.eq_principle = EqPrincipleNamespace()
    gl_mod.message = MessageProxy()
    gl_mod.UserError = UserError
    gl_mod.Address = Address
    gl_mod.u256 = u256
    gl_mod.u64 = u64
    gl_mod.u32 = u32
    gl_mod.TreeMap = TreeMap
    gl_mod.allow_storage = allow_storage

    # Nested gl object
    gl_object = types.SimpleNamespace(
        Contract=Contract,
        public=gl_mod.public,
        nondet=gl_mod.nondet,
        eq_principle=gl_mod.eq_principle,
        message=gl_mod.message,
        UserError=UserError,
    )
    gl_mod.gl = gl_object

    sys.modules["genlayer"] = gl_mod
    return gl_mod

_ensure_genlayer_module()


# ─────────────────────────────────────────────────────────────
# 4. Pytest Fixtures
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def direct_vm():
    _CURRENT_VM.clear_mocks()
    _CURRENT_VM.sender = "0xaaaa111122223333444455556666777788889999"
    _CURRENT_VM.value = u256(0)
    return _CURRENT_VM


@pytest.fixture
def direct_alice():
    return Address("0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")


@pytest.fixture
def direct_bob():
    return Address("0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")


@pytest.fixture
def direct_charlie():
    return Address("0xcccccccccccccccccccccccccccccccccccccccc")


@pytest.fixture
def direct_deploy(direct_vm):
    def deploy_fn(contract_path: str, *args, **kwargs):
        from contracts.breach_registry import SemanticBreachRegistry
        instance = SemanticBreachRegistry(*args, **kwargs)
        return instance
    return deploy_fn
