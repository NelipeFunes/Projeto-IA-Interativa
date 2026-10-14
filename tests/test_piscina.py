"""Piscina Tholz pela nuvem (T-017, etapa 1): configuração do backend, login, sessão e leitura. Tudo com dados falsos."""

from __future__ import annotations

import base64
import json
import logging
import sys
import types

import httpx
import pytest
import respx

from vision import cli, piscina
from vision.arquivos import gravar_json, ler_json
from vision.piscina import ErroPiscina, Piscina, SemConfiguracao, SemLogin, SessaoVencida

POOL = "us-east-1_FalsoPool1"
CLIENT = "ClienteFalsoParaTeste0123"
API = "https://abcd1234xy.execute-api.us-east-1.amazonaws.com/master"
COGNITO = "https://cognito-idp.us-east-1.amazonaws.com/"
SENHA = "-".join(["valor", "falso", "nao", "vazar"])  # montado em partes: não é uma senha de verdade
EMAIL = "pessoa@exemplo.com"


def _jwt(exp: float) -> str:
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    return f"{b64({'alg': 'none'})}.{b64({'exp': exp})}.assinatura"


@pytest.fixture
def app(cfg):
    gravar_json(cfg.dados / "piscina" / "app.json", {"pool_id": POOL, "client_id": CLIENT, "regiao": "us-east-1",
                                                     "api_url": API})
    return cfg


def _sessao(cfg, expira: float, id_token: str = "id-velho", refresh: str = "refresh-1"):
    gravar_json(cfg.dados / "piscina" / "sessao.json", {"id_token": id_token, "access_token": "acesso",
                                                         "refresh_token": refresh, "expira": expira})


class Relogio:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


# ------------------------------------------------------------------ configuração do backend

def test_sem_app_json_ou_incompleto(cfg):
    with pytest.raises(SemConfiguracao, match="app.json"):
        piscina.ler_app(cfg)
    gravar_json(cfg.dados / "piscina" / "app.json", {"pool_id": POOL})
    with pytest.raises(SemConfiguracao, match="incompleto"):
        piscina.ler_app(cfg)


def test_app_valido_e_o_cognito_vem_da_regiao(app):
    a = piscina.ler_app(app)
    assert (a.pool_id, a.client_id, a.api_url) == (POOL, CLIENT, API)
    assert a.cognito_url == COGNITO


@pytest.mark.parametrize("url", [
    "http://abcd1234xy.execute-api.us-east-1.amazonaws.com/master",        # sem https
    "https://exemplo.com/master",                                           # não é a AWS
    "https://abcd1234xy.execute-api.us-east-1.amazonaws.com.mal.com/m",     # domínio parecido
    "https://x@abcd1234xy.execute-api.us-east-1.amazonaws.com/master",      # usuário na URL
    "https://abcd1234xy.execute-api.us-east-1.amazonaws.com:8443/master",   # porta
    "https://abcd1234xy.execute-api.us-east-1.amazonaws.com/master?a=1",    # query
    "https://abcd1234xy.execute-api.us-east-1.amazonaws.com/a/b",           # caminho com barra
    "https://abcd1234xy.execute-api.us-west-2.amazonaws.com/master",        # outra região
    "https://localhost/master", "https://192.168.0.5/master",
])
def test_endereco_da_api_fora_do_padrao_e_recusado(cfg, url):
    gravar_json(cfg.dados / "piscina" / "app.json", {"pool_id": POOL, "client_id": CLIENT, "regiao": "us-east-1",
                                                     "api_url": url})
    with pytest.raises(SemConfiguracao):
        piscina.ler_app(cfg)


@pytest.mark.parametrize("pool,regiao", [("us-east-\u0661_FalsoPool1", "us-east-\u0661"),  # dígito unicode
                                         ("us-east-1_FalsoPool1", "us-east-1\n")])
def test_regiao_e_pool_so_aceitam_ascii(cfg, pool, regiao):
    gravar_json(cfg.dados / "piscina" / "app.json", {"pool_id": pool, "client_id": CLIENT, "regiao": regiao,
                                                     "api_url": API})
    with pytest.raises(SemConfiguracao):
        piscina.ler_app(cfg)


def test_regiao_do_pool_precisa_bater(cfg):
    gravar_json(cfg.dados / "piscina" / "app.json", {"pool_id": "us-west-2_FalsoPool1", "client_id": CLIENT,
                                                     "regiao": "us-east-1", "api_url": API})
    with pytest.raises(SemConfiguracao, match="região"):
        piscina.ler_app(cfg)


# ------------------------------------------------------------------ login

class CognitoFalso:
    """No lugar do pycognito: guarda o que recebeu e devolve tokens, ou levanta o erro pedido."""

    ultimo = None
    erro: Exception | None = None

    def __init__(self, pool, client, regiao, email):
        self.args = (pool, client, regiao, email)
        self.senha = None
        CognitoFalso.ultimo = self

    def authenticate(self, password):
        self.senha = password
        if CognitoFalso.erro:
            raise CognitoFalso.erro
        self.id_token, self.access_token, self.refresh_token = _jwt(2_000_000.0), "acesso-novo", "refresh-novo"


@pytest.fixture(autouse=True)
def _zera_cognito():
    CognitoFalso.ultimo, CognitoFalso.erro = None, None


async def test_login_grava_a_sessao_e_nunca_a_senha_nem_loga_token(app, caplog):
    with caplog.at_level(logging.DEBUG):
        await piscina.login(app, f" {EMAIL} ", SENHA, fabrica=CognitoFalso)
    assert CognitoFalso.ultimo.args == (POOL, CLIENT, "us-east-1", EMAIL) and CognitoFalso.ultimo.senha == SENHA
    s = piscina.ler_sessao(app)
    assert s["refresh_token"] == "refresh-novo" and s["expira"] == 2_000_000.0 and piscina.tem_login(app)
    assert "access_token" not in s  # o que não se usa não fica no disco
    gravado = (app.dados / "piscina" / "sessao.json").read_text(encoding="utf-8")
    assert SENHA not in gravado and EMAIL not in gravado  # nem a senha nem o e-mail ficam no disco
    assert SENHA not in caplog.text and "refresh-novo" not in caplog.text and "acesso-novo" not in caplog.text


@pytest.mark.parametrize("email,senha", [("sem-arroba", SENHA), ("a b@x.com", SENHA), ("", SENHA), (EMAIL, ""),
                                         (EMAIL, "x\ny"), (EMAIL, "x" * 300), (None, SENHA), (EMAIL, None)])
async def test_login_com_entrada_invalida_nem_chama_o_servico(app, email, senha):
    with pytest.raises(ErroPiscina):
        await piscina.login(app, email, senha, fabrica=CognitoFalso)
    assert CognitoFalso.ultimo is None and not piscina.tem_login(app)


class ErroDeConta(Exception):
    def __init__(self, codigo):
        super().__init__(f"detalhe interno com {SENHA}")
        self.response = {"Error": {"Code": codigo}}


@pytest.mark.parametrize("erro,trecho", [
    (ErroDeConta("NotAuthorizedException"), "incorretos"),
    (ErroDeConta("UserNotFoundException"), "incorretos"),
    (ErroDeConta("UserNotConfirmedException"), "confirmada"),
    (type("SMSMFAChallengeException", (Exception,), {})("x"), "etapa extra"),
    (ErroDeConta("OutroErro"), "OutroErro"),
    (RuntimeError(f"vazou {SENHA}"), "RuntimeError"),
])
async def test_erros_de_login_viram_mensagem_fixa_sem_a_senha(app, erro, trecho):
    CognitoFalso.erro = erro
    with pytest.raises(ErroPiscina, match=trecho) as e:
        await piscina.login(app, EMAIL, SENHA, fabrica=CognitoFalso)
    assert SENHA not in str(e.value) and e.value.__cause__ is None and not piscina.tem_login(app)


# ------------------------------------------------------------------ leitura

async def _piscina(cfg, relogio=None, transporte=None) -> Piscina:
    return Piscina(cfg, relogio=relogio or Relogio(), transporte=transporte)


@respx.mock
async def test_sem_login_nao_chama_a_nuvem(app):
    rota = respx.route().mock(return_value=httpx.Response(200, json={}))
    p = await _piscina(app)
    with pytest.raises(SemLogin, match="piscina-login"):
        await p.ler("dispositivos")
    assert not rota.called
    await p.fechar()


@respx.mock
async def test_le_os_dispositivos_com_o_token_puro_e_so_no_endereco_do_app(app):
    _sessao(app, expira=2_000_000.0, id_token="id-bom")
    rota = respx.get(f"{API}/device").mock(return_value=httpx.Response(200, json=[{"nome": "x", "tipo": "pool"}]))
    p = await _piscina(app)
    assert await p.ler("dispositivos") == [{"nome": "x", "tipo": "pool"}]
    assert rota.calls[0].request.headers["Authorization"] == "id-bom"
    assert {str(c.request.url.host) for c in respx.calls} == {"abcd1234xy.execute-api.us-east-1.amazonaws.com"}
    await p.fechar()


@respx.mock
async def test_leitura_desconhecida_e_recusada_sem_chamar(app):
    _sessao(app, expira=2_000_000.0)
    rota = respx.route().mock(return_value=httpx.Response(200, json={}))
    p = await _piscina(app)
    with pytest.raises(ErroPiscina, match="desconhecida"):
        await p.ler("../../etc/passwd")
    assert not rota.called
    await p.fechar()


@respx.mock
async def test_token_perto_de_vencer_e_renovado_antes(app):
    rel = Relogio(1_000_000.0)
    _sessao(app, expira=rel() + 30)  # menos de 2 min
    novo = _jwt(rel() + 3600)
    renova = respx.post(COGNITO).mock(return_value=httpx.Response(200, json={
        "AuthenticationResult": {"IdToken": novo, "AccessToken": "acesso-2", "ExpiresIn": 3600}}))
    rota = respx.get(f"{API}/device").mock(return_value=httpx.Response(200, json=[]))
    p = await _piscina(app, rel)
    await p.ler("dispositivos")
    assert renova.called and rota.calls[0].request.headers["Authorization"] == novo
    corpo = json.loads(renova.calls[0].request.content)
    assert corpo["AuthFlow"] == "REFRESH_TOKEN_AUTH" and corpo["ClientId"] == CLIENT
    s = piscina.ler_sessao(app)
    assert s["id_token"] == novo and s["refresh_token"] == "refresh-1"  # o refresh token continua o mesmo
    await p.fechar()


@respx.mock
async def test_refresh_recusado_apaga_a_sessao_e_pede_login(app):
    rel = Relogio()
    _sessao(app, expira=rel() - 5)
    respx.post(COGNITO).mock(return_value=httpx.Response(400, json={"__type": "NotAuthorizedException"}))
    p = await _piscina(app, rel)
    with pytest.raises(SessaoVencida, match="piscina-login"):
        await p.ler("dispositivos")
    assert not piscina.tem_login(app)
    await p.fechar()


@respx.mock
async def test_cognito_fora_do_ar_nao_apaga_a_sessao(app):
    rel = Relogio()
    _sessao(app, expira=rel() - 5)
    respx.post(COGNITO).mock(return_value=httpx.Response(503))
    p = await _piscina(app, rel)
    with pytest.raises(ErroPiscina, match="não renovou"):
        await p.ler("dispositivos")
    assert piscina.tem_login(app)  # um erro passageiro não derruba o login
    await p.fechar()


@respx.mock
async def test_401_renova_uma_vez_e_depois_tenta_bearer(app):
    rel = Relogio()
    _sessao(app, expira=rel() + 3600)
    novo = _jwt(rel() + 3600)
    respx.post(COGNITO).mock(return_value=httpx.Response(200, json={"AuthenticationResult": {"IdToken": novo}}))

    def responde(request):
        return httpx.Response(200, json=["ok"]) if request.headers["Authorization"] == f"Bearer {novo}" \
            else httpx.Response(401)

    rota = respx.get(f"{API}/device").mock(side_effect=responde)
    p = await _piscina(app, rel)
    assert await p.ler("dispositivos") == ["ok"]
    assert [c.request.headers["Authorization"] for c in rota.calls] == ["id-velho", novo, f"Bearer {novo}"]
    await p.ler("dispositivos")
    assert rota.calls[-1].request.headers["Authorization"] == f"Bearer {novo}"  # lembra o que funcionou
    await p.fechar()


@respx.mock
async def test_401_em_tudo_vira_sessao_vencida(app):
    rel = Relogio()
    _sessao(app, expira=rel() + 3600)
    respx.post(COGNITO).mock(return_value=httpx.Response(200, json={"AuthenticationResult": {"IdToken": _jwt(9e9)}}))
    respx.get(f"{API}/device").mock(return_value=httpx.Response(403))
    p = await _piscina(app, rel)
    with pytest.raises(SessaoVencida):
        await p.ler("dispositivos")
    await p.fechar()


@respx.mock
async def test_404_tenta_o_proximo_caminho_e_depois_desiste(app):
    _sessao(app, expira=2_000_000.0)
    respx.get(f"{API}/device").mock(return_value=httpx.Response(404))
    segunda = respx.get(f"{API}/devices").mock(return_value=httpx.Response(200, json={"itens": []}))
    p = await _piscina(app)
    assert await p.ler("dispositivos") == {"itens": []} and segunda.called
    segunda.mock(return_value=httpx.Response(404))
    with pytest.raises(ErroPiscina, match="palpites"):
        await p.ler("dispositivos")
    await p.fechar()


@respx.mock
@pytest.mark.parametrize("resposta,trecho", [
    (httpx.Response(500), "HTTP 500"),
    (httpx.Response(200, text="<html>não é json"), "não é JSON"),
])
async def test_erros_da_api_viram_mensagem_sem_o_endereco(app, resposta, trecho):
    _sessao(app, expira=2_000_000.0)
    respx.get(f"{API}/device").mock(return_value=resposta)
    p = await _piscina(app)
    with pytest.raises(ErroPiscina, match=trecho) as e:
        await p.ler("dispositivos")
    assert "execute-api" not in str(e.value)
    await p.fechar()


@respx.mock
async def test_rede_fora_do_ar_vira_mensagem_sem_o_endereco(app):
    _sessao(app, expira=2_000_000.0)
    respx.get(f"{API}/device").mock(side_effect=httpx.ConnectError("sem rota para abcd1234xy"))
    p = await _piscina(app)
    with pytest.raises(ErroPiscina, match="ConnectError") as e:
        await p.ler("dispositivos")
    assert "abcd1234xy" not in str(e.value)
    await p.fechar()


@respx.mock
async def test_nao_segue_redirecionamento_para_outro_servidor(app):
    _sessao(app, expira=2_000_000.0)
    respx.get(f"{API}/device").mock(return_value=httpx.Response(302, headers={"Location": "https://mal.example/x"}))
    outro = respx.get("https://mal.example/x").mock(return_value=httpx.Response(200, json={}))
    p = await _piscina(app)
    with pytest.raises(ErroPiscina, match="HTTP 302"):
        await p.ler("dispositivos")
    assert not outro.called  # o token nunca vai para outro endereço
    await p.fechar()


# ------------------------------------------------------------------ formato e comandos

def test_forma_nao_vaza_valores():
    dados = [{"nome": "Piscina da Casa", "id": "abc-123-secreto", "ligado": True, "temp": 27.5, "x": None,
              "sub": {"a": [1, 2, 3]}}, {"nome": "outro"}]
    f = json.dumps(piscina.forma(dados), ensure_ascii=False)
    assert "Piscina da Casa" not in f and "secreto" not in f and "27.5" not in f
    assert "texto(15)" in f and "bool" in f and "número" in f and "nulo" in f and "2 itens" in f
    assert piscina.forma([]) == [] and piscina.forma({"a": {"b": {"c": {"d": {"e": 1}}}}}) == {"a": {"b": {"c": {"d": "..."}}}}


async def test_ler_interativo_guarda_o_json_cru_e_mostra_so_o_formato(app, monkeypatch, capsys):
    class Falsa:
        def __init__(self, cfg):
            pass

        async def ler(self, o_que):
            return [{"nome": "Segredo da Casa", "id": "xyz"}]

        async def fechar(self):
            pass

    monkeypatch.setattr(piscina, "Piscina", Falsa)
    assert await piscina.ler_interativo(app) == 0
    saida = capsys.readouterr().out
    assert "Segredo da Casa" not in saida and "texto(15)" in saida
    assert ler_json(app.dados / "piscina" / "ultima-leitura.json") == [{"nome": "Segredo da Casa", "id": "xyz"}]


async def test_comandos_sem_configuracao_dizem_o_que_falta(cfg, capsys):
    assert await piscina.ler_interativo(cfg) == 1
    assert await piscina.login_interativo(cfg) == 1
    assert capsys.readouterr().out.count("app.json") == 2


def test_cli_conhece_os_comandos_e_silencia_os_logs_do_login(cfg, monkeypatch):
    chamados = []

    async def login_falso(c):
        chamados.append("login")
        return 0

    async def ler_falso(c):
        chamados.append("ler")
        return 0

    monkeypatch.setattr(cli.config, "carregar", lambda **_: cfg)
    monkeypatch.setattr(piscina, "login_interativo", login_falso)
    monkeypatch.setattr(piscina, "ler_interativo", ler_falso)
    assert cli.main(["piscina-login"]) == 0 and cli.main(["piscina-ler"]) == 0
    assert chamados == ["login", "ler"]
    for nome in ("botocore", "boto3", "pycognito"):
        assert logging.getLogger(nome).getEffectiveLevel() >= logging.WARNING


# ------------------------------------------------------------------ revisão do PR 57

@respx.mock
async def test_403_de_rota_inexistente_nao_e_sessao_vencida_e_tenta_o_proximo_caminho(app):
    """O API Gateway responde 403 "Missing Authentication Token" a um caminho que não existe."""
    _sessao(app, expira=2_000_000.0)
    renova = respx.post(COGNITO).mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{API}/device").mock(return_value=httpx.Response(
        403, json={"message": "Missing Authentication Token"}, headers={"x-amzn-ErrorType": "MissingAuthenticationTokenException"}))
    segunda = respx.get(f"{API}/devices").mock(return_value=httpx.Response(200, json=["ok"]))
    p = await _piscina(app)
    assert await p.ler("dispositivos") == ["ok"] and segunda.called
    assert not renova.called  # nem renovou o token nem trocou para Bearer por causa de uma rota que não existe
    segunda.mock(return_value=httpx.Response(403, text='{"message":"Missing Authentication Token"}'))
    with pytest.raises(ErroPiscina, match="palpites") as e:
        await p.ler("dispositivos")
    assert not isinstance(e.value, SessaoVencida)
    await p.fechar()


@respx.mock
@pytest.mark.parametrize("resposta", [
    httpx.Response(400, json={"__type": "TooManyRequestsException"}),
    httpx.Response(400, json={"__type": "LimitExceededException"}),
    httpx.Response(403, text="bloqueado pelo proxy"),
    httpx.Response(200, text="<html>portal cativo</html>"),
    httpx.Response(200, json={"AuthenticationResult": {}}),
    httpx.Response(302, headers={"Location": "https://mal.example"}),
])
async def test_erro_passageiro_na_renovacao_nao_apaga_a_sessao(app, resposta):
    rel = Relogio()
    _sessao(app, expira=rel() - 5)
    respx.post(COGNITO).mock(return_value=resposta)
    p = await _piscina(app, rel)
    with pytest.raises(ErroPiscina) as e:
        await p.ler("dispositivos")
    assert not isinstance(e.value, SessaoVencida) and piscina.tem_login(app)
    await p.fechar()


@respx.mock
@pytest.mark.parametrize("tipo", ["NotAuthorizedException", "com.amazonaws#NotAuthorizedException",
                                  "UserNotFoundException"])
async def test_so_a_recusa_do_refresh_token_apaga_a_sessao(app, tipo):
    rel = Relogio()
    _sessao(app, expira=rel() - 5)
    respx.post(COGNITO).mock(return_value=httpx.Response(400, json={"__type": tipo}))
    p = await _piscina(app, rel)
    with pytest.raises(SessaoVencida):
        await p.ler("dispositivos")
    assert not piscina.tem_login(app)
    await p.fechar()


@respx.mock
async def test_refresh_token_novo_da_rotacao_e_guardado(app):
    rel = Relogio()
    _sessao(app, expira=rel() - 5, refresh="refresh-velho")
    novo = _jwt(rel() + 3600)
    respx.post(COGNITO).mock(return_value=httpx.Response(200, json={
        "AuthenticationResult": {"IdToken": novo, "RefreshToken": "refresh-rodado"}}))
    respx.get(f"{API}/device").mock(return_value=httpx.Response(200, json=[]))
    p = await _piscina(app, rel)
    await p.ler("dispositivos")
    assert piscina.ler_sessao(app)["refresh_token"] == "refresh-rodado"
    assert "access_token" not in piscina.ler_sessao(app)
    await p.fechar()


async def test_caminho_real_do_login_fixa_regiao_endpoint_e_nao_assina(app, monkeypatch):
    """O ramo sem fábrica (o do uso de verdade), com o pycognito e o botocore simulados."""
    visto = {}

    class CognitoSimulado:
        def __init__(self, pool, client, **kw):
            visto.update(pool=pool, client=client, **kw)
            self.id_token, self.refresh_token = _jwt(2_000_000.0), "refresh-simulado"

        def authenticate(self, password):
            visto["senha_recebida"] = password == SENHA

    unsigned = object()
    modulos = {"pycognito": types.SimpleNamespace(Cognito=CognitoSimulado),
               "botocore": types.SimpleNamespace(UNSIGNED=unsigned),
               "botocore.config": types.SimpleNamespace(Config=lambda **kw: ("config", kw))}
    for nome, mod in modulos.items():
        monkeypatch.setitem(sys.modules, nome, mod)
    await piscina.login(app, EMAIL, SENHA)
    assert visto["pool"] == POOL and visto["client"] == CLIENT and visto["user_pool_region"] == "us-east-1"
    assert visto["boto3_client_kwargs"] == {"endpoint_url": COGNITO}  # AWS_ENDPOINT_URL* não redireciona o login
    assert visto["botocore_config"] == ("config", {"signature_version": unsigned})  # sem credenciais da AWS
    assert visto["senha_recebida"] and piscina.ler_sessao(app)["refresh_token"] == "refresh-simulado"


async def test_erro_de_disco_ao_gravar_a_sessao_vira_mensagem(app, monkeypatch):
    def quebra(*a, **k):
        raise PermissionError("arquivo em uso")

    monkeypatch.setattr(piscina, "gravar_json", quebra)
    with pytest.raises(ErroPiscina, match="gravar a sessão"):
        await piscina.login(app, EMAIL, SENHA, fabrica=CognitoFalso)


def test_forma_mascara_chaves_que_parecem_identificador():
    dados = {"4f3c2a10-aaaa-bbbb-cccc-1234567890ab": 1, "a1:b2:c3:d4:e5:f6": 2, "pessoa@exemplo.com": 3,
             "nomeDoDispositivo": 4, "ligado": 5, "AbCdEfGhIjKlMnOp9": 6}
    f = piscina.forma(dados)
    assert set(f) == {"<id>", "<id>+", "<id>++", "<id>+++", "nomeDoDispositivo", "ligado"}
    texto = json.dumps(f)
    assert "4f3c2a10" not in texto and "a1:b2" not in texto and "pessoa@" not in texto


async def test_ctrl_c_e_fim_de_entrada_no_login_cancelam_sem_traceback(app, monkeypatch, capsys):
    for erro in (KeyboardInterrupt, EOFError):
        def levanta(*a, _erro=erro, **k):
            raise _erro()

        monkeypatch.setattr("builtins.input", levanta)
        assert await piscina.login_interativo(app) == 1
    assert capsys.readouterr().out.count("Cancelado") == 2
