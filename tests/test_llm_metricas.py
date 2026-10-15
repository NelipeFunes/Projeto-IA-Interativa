"""O que o Ollama mede de cada pedido (tokens do prompt, custo do prefill, carga do modelo) chega em `RespostaLLM`."""

from __future__ import annotations

from types import SimpleNamespace

from vision.brain.llm import OllamaLLM


def _parte(texto="", done=False, **medidas):
    base = dict(eval_count=7, total_duration=2_000_000_000, prompt_eval_count=7800, prompt_eval_duration=300_000_000,
                load_duration=5_000_000)
    base.update(medidas)
    return SimpleNamespace(message=SimpleNamespace(content=texto, tool_calls=None), done=done, **base)


class ClienteFalso:
    def __init__(self, **medidas):
        self.medidas = medidas

    async def chat(self, **kw):
        if not kw.get("stream"):
            return _parte("oi", True, **self.medidas)

        async def fluxo():
            yield _parte("o")
            yield _parte("i", True, **self.medidas)

        return fluxo()


def _llm(**medidas) -> OllamaLLM:
    llm = OllamaLLM("falso", "http://127.0.0.1:1", False, 4096, 0.3, "-1m")
    llm.cliente = ClienteFalso(**medidas)
    return llm


async def test_sem_streaming_traz_as_medidas_do_prompt():
    r = await _llm().conversar([{"role": "user", "content": "oi"}], None)
    assert (r.prompt_tokens, r.prompt_s, r.carga_s, r.tokens) == (7800, 0.3, 0.005, 7)


async def test_com_streaming_traz_as_medidas_do_ultimo_pedaco():
    r = await _llm().conversar([{"role": "user", "content": "oi"}], None, ao_texto=lambda _t: None)
    assert r.texto == "oi" and (r.prompt_tokens, r.prompt_s, r.carga_s, r.tokens) == (7800, 0.3, 0.005, 7)


async def test_medida_ausente_vira_zero_e_nao_quebra():
    r = await _llm(prompt_eval_count=None, prompt_eval_duration=None, load_duration=None).conversar([], None)
    assert (r.prompt_tokens, r.prompt_s, r.carga_s) == (0, 0.0, 0.0)
