"""
Shared test bootstrap - wires up the offline genlayer SDK stub and
loads contract.py once.

Simplification note: earlier contracts in this portfolio nested the
stub as tests/genlayer_stub/genlayer/__init__.py (a real package
named `genlayer`) so that `from genlayer import *` in contract.py
resolved naturally via sys.path. That nested-folder structure is
awkward to create through GitHub's mobile "create file" flow, so this
version instead loads a single flat file (genlayer_stub.py) and
registers it directly into sys.modules under the name "genlayer" -
same effect, one file instead of a nested package.
"""
import importlib.util
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))

_stub_path = os.path.join(_THIS_DIR, "genlayer_stub.py")
_stub_spec = importlib.util.spec_from_file_location("genlayer", _stub_path)
_stub_module = importlib.util.module_from_spec(_stub_spec)
sys.modules["genlayer"] = _stub_module
_stub_spec.loader.exec_module(_stub_module)

_CONTRACT_PATH = os.path.join(os.path.dirname(_THIS_DIR), "contract.py")
_spec = importlib.util.spec_from_file_location("weathervault_contract", _CONTRACT_PATH)
_contract_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract_module)

WeatherVault = _contract_module.WeatherVault
gl = _contract_module.gl

from genlayer import tx_context, Address, _TransferRecorder  # noqa: E402
import datetime as _real_datetime_module  # noqa: E402


def make_contract() -> "WeatherVault":
    return WeatherVault()


def transfers():
    return _TransferRecorder.calls


def reset_transfers():
    _TransferRecorder.reset()


class _ControllableDatetime:
    """
    TEST-ONLY stand-in for the `datetime` class contract.py imports.
    Swapped into the loaded contract module's namespace so tests can
    pin "now" to an exact value (simulating elapsed time for
    expire_policy) without needing a real GenVM. `strptime` delegates
    to the real implementation so stored event_date strings still
    parse correctly.
    """

    _current = None

    @classmethod
    def now(cls):
        if cls._current is not None:
            return cls._current
        return _real_datetime_module.datetime.now()

    @staticmethod
    def strptime(*args, **kwargs):
        return _real_datetime_module.datetime.strptime(*args, **kwargs)


_contract_module.datetime = _ControllableDatetime


def set_now(dt):
    """Pin the contract's notion of 'now' to an exact datetime for this test."""
    _ControllableDatetime._current = dt


def reset_now():
    """Return the contract's notion of 'now' to the real wall clock."""
    _ControllableDatetime._current = None
