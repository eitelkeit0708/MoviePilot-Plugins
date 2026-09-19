"""Only permitted host-private adapter: two V3.0.4 Meta result bridges."""
import inspect
from threading import RLock

_INSTALL_LOCK = RLock()


class MetaPatch:
    def __init__(self, corrector, host=None):
        self.corrector, self.host = corrector, host
        self.active = False
        self.state = "OFF"
        self.originals = {}
        self.wrappers = {}
        self.lock = RLock()
        self.last_reason = None

    def _owns(self):
        return all(getattr(self.host, name, None) is wrapper for name, wrapper in self.wrappers.items())

    def install(self, version):
        with _INSTALL_LOCK, self.lock:
            if self.state == "CONFLICT":
                return False
            if self.active:
                if self._owns():
                    return True
                self.active, self.state = False, "CONFLICT"
                return False
            if str(version).removeprefix("v") != "3.0.4":
                self.state = "VERSION_UNSUPPORTED"
                return False
            if self.host is None:
                import app.domain.metainfo as host
                self.host = host
            expected = {"_build_python_meta_info": ("title", "subtitle", "custom_words"),
                        "_meta_from_rust": ("parsed",)}
            for name, parameters in expected.items():
                func = getattr(self.host, name, None)
                if not callable(func) or getattr(func, "_subscribetter_owner", None) is not None:
                    self.state = "CONFLICT"
                    return False
                if getattr(self.host, "__name__", None) == "app.domain.metainfo" and (
                        getattr(func, "__module__", None) != "app.domain.metainfo" or
                        getattr(func, "__name__", None) != name):
                    self.state = "CONFLICT"
                    return False
                signature = inspect.signature(func, follow_wrapped=False)
                args = list(signature.parameters.values())
                if (tuple(p.name for p in args) != parameters or
                        any(p.kind != inspect.Parameter.POSITIONAL_OR_KEYWORD for p in args) or
                        args[0].default is not inspect.Parameter.empty or
                        any(p.default is not None for p in args[1:])):
                    self.state = "SIGNATURE_UNSUPPORTED"
                    return False
            self.originals = {name: getattr(self.host, name) for name in expected}
            def python(title, subtitle=None, custom_words=None):
                native = self.originals["_build_python_meta_info"](title, subtitle, custom_words)
                return self._correct(native, title, subtitle, custom_words)
            def rust(parsed):
                native = self.originals["_meta_from_rust"](parsed)
                if native is None:
                    return None
                return self._correct(native, parsed.get("title") or "", parsed.get("subtitle"), context_known=False)
            self.wrappers = {"_build_python_meta_info": python, "_meta_from_rust": rust}
            for name, wrapper in self.wrappers.items():
                wrapper._subscribetter_owner = self
                setattr(self.host, name, wrapper)
            self.active, self.state = True, "ACTIVE"
            return True

    def _correct(self, native, title, subtitle=None, custom_words=None, *, context_known=True):
        if not self.active:
            return native
        if not self._owns():
            self.active, self.state = False, "CONFLICT"
            return native
        try:
            result = self.corrector.correct(native, title, subtitle, custom_words, context_known=context_known)
            self.last_reason = result.reasons[-1] if result.reasons else None
            if result.status == "ERROR":
                self.active, self.state = False, "RESULT_UNSUPPORTED"
                return native
            return result.meta
        except Exception:
            self.last_reason = "CORRECTION_FAILED"
            return native

    def uninstall(self):
        with _INSTALL_LOCK, self.lock:
            self.active = False
            conflict = False
            for name, wrapper in self.wrappers.items():
                if getattr(self.host, name, None) is wrapper:
                    setattr(self.host, name, self.originals[name])
                else:
                    conflict = True
            self.state = "CONFLICT" if conflict else "OFF"

    def diagnostics(self):
        if self.active and not self._owns():
            self.active, self.state = False, "CONFLICT"
        return {"state": self.state, "active": self.active, "revision": self.corrector.revision,
                "last_reason": self.last_reason,
                "coverage": "Two bridges only; Python path merges later; direct MetaVideo and music bypass; Rust lock context unavailable"}
