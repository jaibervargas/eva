from pathlib import Path

from app.setup_inicial import preparar_modelos, servidor_disponible


class Respuesta:
    def raise_for_status(self):
        return None


class ClienteFalso:
    def __init__(self, disponible=True):
        self.disponible = disponible
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        if not self.disponible:
            raise OSError("apagado")
        return Respuesta()


class ModeloFalso:
    def __init__(self, necesita=True):
        self.necesita = necesita
        self.cargas = 0

    def necesita_descarga(self):
        return self.necesita

    def cargar(self):
        self.cargas += 1


def test_servidor_disponible_uses_the_models_endpoint_without_raising():
    cliente = ClienteFalso()

    assert servidor_disponible("http://localhost:1234/v1", cliente=cliente) is True
    assert cliente.urls == ["http://localhost:1234/v1/models"]


def test_unavailable_server_is_a_reported_false_result():
    assert servidor_disponible("http://localhost:1234/v1", cliente=ClienteFalso(False)) is False


def test_preparar_modelos_reports_progress_and_loads_missing_models():
    piper = ModeloFalso()
    whisper = ModeloFalso()
    vistos = []

    resultado = preparar_modelos(piper, whisper, progreso=vistos.append)

    assert resultado == {"voz": True, "transcripcion": True}
    assert piper.cargas == whisper.cargas == 1
    assert vistos == [
        "Descargando la voz de Eva…",
        "Descargando el modelo de voz…",
    ]


def test_preparar_modelos_does_not_reload_existing_models():
    piper = ModeloFalso(False)
    whisper = ModeloFalso(False)

    resultado = preparar_modelos(piper, whisper)

    assert resultado == {"voz": False, "transcripcion": False}
    assert piper.cargas == whisper.cargas == 0
