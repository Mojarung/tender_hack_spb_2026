__version__ = "0.1.0"

# ---------------------------------------------------------------------------
# Allow reserved / special-use TLDs (.local / .test / .example) in emails.
#
# pydantic.EmailStr is a compiled rust-side validator (pydantic-core) that
# cannot be monkey-patched in Python land. So we swap the EmailStr type
# itself for plain `str` BEFORE any downstream module (fastapi-users
# schemas, ours, etc.) imports it — they will see `str` and validate
# nothing TLD-related. Format is still checked at the API edge by our
# own regex in `pricepulse.auth.schemas`.
# ---------------------------------------------------------------------------
try:
    import pydantic
    if pydantic.EmailStr is not str:        # type: ignore[attr-defined]
        pydantic.EmailStr = str             # type: ignore[assignment]
        try:
            from pydantic import networks as _pn
            _pn.EmailStr = str              # type: ignore[assignment]
        except ImportError:
            pass
except ImportError:  # pragma: no cover
    pass
