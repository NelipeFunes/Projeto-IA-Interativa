"""O que a tela de ajustes da janela pode ler e mudar. Só estas chaves, e tudo validado aqui.

O que você muda fica em data/config-local.yaml (fora do git), por cima do config.yaml.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from vision.config import Config


@dataclass(frozen=True)
class Ajuste:
    chave: str
    tipo: str  # "texto" | "numero" | "escolha"
    rotulo: str
    ao_vivo: bool  # vale na hora; senão, depois de reiniciar o núcleo
    minimo: float | None = None
    maximo: float | None = None


LISTA = [
    Ajuste("assistente.nome", "texto", "Nome do assistente (a ativação continua \"Hey Vision\")", ao_vivo=False),
    Ajuste("voz.voz_piper", "escolha", "Voz", ao_vivo=True),
    Ajuste("voz.velocidade_fala", "numero", "Ritmo da fala (maior = mais devagar)", ao_vivo=True, minimo=0.7, maximo=1.5),
    Ajuste("voz.conversa_silencio_max_s", "numero", "Fecha a conversa depois de quantos segundos sem falar",
           ao_vivo=True, minimo=30, maximo=600),
    Ajuste("voz.microfone", "escolha", "Microfone", ao_vivo=False),
]
POR_CHAVE = {a.chave: a for a in LISTA}
NOME_VALIDO = re.compile(r"^[^\W\d_](?:[^\W_]| ){0,19}$")  # letras (com acento), dígitos e espaço; até 20


def vozes(cfg: Config) -> list[str]:
    return sorted(p.stem for p in (cfg.modelos / "piper").glob("pt_BR-*.onnx"))


def microfones() -> list[str]:
    """Nomes dos microfones (mesma API de áudio que o laço usa). Sem placa de som: lista vazia."""
    try:
        import sounddevice as sd

        from vision.voice.audio import _mme

        api = _mme()
        nomes = [d["name"] for d in sd.query_devices()
                 if d["max_input_channels"] > 0 and (api is None or d["hostapi"] == api)]
    except Exception:  # noqa: BLE001
        return []
    return list(dict.fromkeys(nomes))


def ler(cfg: Config) -> dict[str, Any]:
    mics = cfg.get("voz.microfone", []) or []
    lista_mics = microfones()
    valores = {a.chave: cfg.get(a.chave) for a in LISTA}
    # O config guarda pedaços do nome ("HyperX"): a tela mostra o primeiro microfone que casa com um deles.
    valores["voz.microfone"] = next((m for p in mics for m in lista_mics if str(p).lower() in m.lower()), None)
    return {
        "valores": valores,
        "campos": [{"chave": a.chave, "tipo": a.tipo, "rotulo": a.rotulo, "aoVivo": a.ao_vivo,
                    "minimo": a.minimo, "maximo": a.maximo} for a in LISTA],
        "opcoes": {"voz.voz_piper": vozes(cfg), "voz.microfone": lista_mics},
    }


def validar(cfg: Config, dados: Any) -> dict[str, Any]:
    """Devolve as mudanças já no formato do config. ValueError com a mensagem para a tela."""
    if not isinstance(dados, dict):
        raise ValueError("ajustes inválidos")
    mudancas: dict[str, Any] = {}
    for chave, valor in dados.items():
        a = POR_CHAVE.get(chave)
        if a is None:
            raise ValueError(f"ajuste desconhecido: {str(chave)[:40]}")
        if a.tipo == "texto":
            texto = str(valor or "").strip() if isinstance(valor, str) else ""
            if not NOME_VALIDO.match(texto):
                raise ValueError("o nome precisa ter de 1 a 20 letras")
            mudancas[chave] = texto
        elif a.tipo == "numero":
            if isinstance(valor, bool) or not isinstance(valor, int | float):
                raise ValueError(f"{a.rotulo}: precisa ser um número")
            if not (a.minimo is not None and a.maximo is not None and a.minimo <= valor <= a.maximo):
                raise ValueError(f"{a.rotulo}: entre {a.minimo:g} e {a.maximo:g}")
            mudancas[chave] = round(float(valor), 2)
        elif chave == "voz.voz_piper":
            if valor not in vozes(cfg):
                raise ValueError("essa voz não está instalada")
            mudancas[chave] = valor
        elif chave == "voz.microfone":
            if valor not in microfones():
                raise ValueError("esse microfone não foi encontrado")
            # Sem os pedaços que já casam com o escolhido: a lista não cresce a cada troca.
            antes = [m for m in (cfg.get("voz.microfone", []) or []) if str(m).lower() not in valor.lower()]
            mudancas[chave] = [valor, *antes]  # o escolhido primeiro; os outros continuam de reserva
    return mudancas


def precisa_reiniciar(mudancas: dict[str, Any], cfg: Config) -> bool:
    return any(not POR_CHAVE[k].ao_vivo and cfg.get(k) != v for k, v in mudancas.items())
