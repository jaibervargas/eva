"""Chat history stored in SQLite (tables ``conversaciones`` and ``mensajes``).

Each operation opens its own connection, so the history can be used from the
UI thread and from the background thread that streams answers.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.llm.base import Mensaje

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS conversaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo TEXT NOT NULL,
    creada TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mensajes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversacion_id INTEGER NOT NULL REFERENCES conversaciones(id) ON DELETE CASCADE,
    rol TEXT NOT NULL CHECK (rol IN ('system', 'user', 'assistant')),
    contenido TEXT NOT NULL,
    creado TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mensajes_conversacion ON mensajes(conversacion_id, id);
"""


@dataclass(frozen=True)
class ResumenConversacion:
    id: int
    titulo: str


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _insertar_mensajes(db: sqlite3.Connection, conversacion_id: int, mensajes: list[Mensaje]) -> None:
    ahora = _ahora()
    db.executemany(
        "INSERT INTO mensajes (conversacion_id, rol, contenido, creado) VALUES (?, ?, ?, ?)",
        [(conversacion_id, m["role"], m["content"], ahora) for m in mensajes],
    )


class Historial:
    def __init__(self, ruta: Path | str) -> None:
        self.ruta = Path(ruta)
        with self._conexion() as db:
            db.executescript(_ESQUEMA)

    @contextmanager
    def _conexion(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.ruta)
        try:
            db.execute("PRAGMA foreign_keys = ON")
            with db:  # commits on success, rolls back on error
                yield db
        finally:
            db.close()

    def crear_conversacion(self, titulo: str) -> int:
        with self._conexion() as db:
            cur = db.execute(
                "INSERT INTO conversaciones (titulo, creada) VALUES (?, ?)", (titulo, _ahora())
            )
            return int(cur.lastrowid)

    def agregar_mensajes(self, conversacion_id: int, mensajes: list[Mensaje]) -> None:
        """Append messages atomically: either all are saved or none."""
        with self._conexion() as db:
            _insertar_mensajes(db, conversacion_id, mensajes)

    def guardar_turno(self, conversacion_id: int | None, titulo: str, mensajes: list[Mensaje]) -> int:
        """Save a turn in one transaction and return the conversation id.

        With ``conversacion_id`` None a conversation titled ``titulo`` is
        created first; if any message fails, nothing is saved.
        """
        with self._conexion() as db:
            if conversacion_id is None:
                cur = db.execute(
                    "INSERT INTO conversaciones (titulo, creada) VALUES (?, ?)", (titulo, _ahora())
                )
                conversacion_id = int(cur.lastrowid)
            _insertar_mensajes(db, conversacion_id, mensajes)
        return conversacion_id

    def mensajes(self, conversacion_id: int, limite: int | None = None) -> list[Mensaje]:
        """Messages of a conversation in chronological order; only the last ``limite`` if given."""
        with self._conexion() as db:
            filas = db.execute(
                "SELECT rol, contenido FROM mensajes WHERE conversacion_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (conversacion_id, -1 if limite is None else limite),
            ).fetchall()
        return [{"role": rol, "content": contenido} for rol, contenido in reversed(filas)]

    def listar_conversaciones(self) -> list[ResumenConversacion]:
        """Conversations, the most recently used first."""
        with self._conexion() as db:
            filas = db.execute(
                "SELECT c.id, c.titulo FROM conversaciones c "
                "LEFT JOIN mensajes m ON m.conversacion_id = c.id "
                "GROUP BY c.id ORDER BY COALESCE(MAX(m.id), 0) DESC, c.id DESC"
            ).fetchall()
        return [ResumenConversacion(id=i, titulo=t) for i, t in filas]
