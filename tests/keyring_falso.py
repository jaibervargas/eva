"""In-memory keyring backends so tests never touch the real OS secret store."""

import keyring.backend
import keyring.errors


class KeyringFalso(keyring.backend.KeyringBackend):
    """Keeps passwords in a dict keyed by (service, username)."""

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.datos: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.datos.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.datos[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        try:
            del self.datos[(service, username)]
        except KeyError:
            raise keyring.errors.PasswordDeleteError("Password not found") from None


class KeyringRoto(keyring.backend.KeyringBackend):
    """Every operation fails, like a locked or missing OS secret store."""

    priority = 1  # type: ignore[assignment]

    def __init__(self, detalle: str = "almacén bloqueado") -> None:
        super().__init__()
        self.detalle = detalle

    def get_password(self, service: str, username: str) -> str | None:
        raise keyring.errors.KeyringError(self.detalle)

    def set_password(self, service: str, username: str, password: str) -> None:
        raise keyring.errors.PasswordSetError(self.detalle)

    def delete_password(self, service: str, username: str) -> None:
        raise keyring.errors.PasswordDeleteError(self.detalle)
