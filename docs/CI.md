# Integração contínua

O workflow `.github/workflows/ci.yml` roda em PRs (inclusive edição da descrição),
push na main e execução manual. Os nove checks com os nomes do checklist validam
declarações do autor da PR; não provam, por si só, a qualidade da IA.
Itens desmarcados, ausentes ou dentro de comentários/exemplos falham.
O conteúdo da PR é lido como dados do evento, nunca como comandos shell.

## Evidências esperadas na revisão

| Critério | Evidência a incluir na PR |
| --- | --- |
| IA funciona sem erros | Comandos e resultados dos testes do fluxo afetado |
| Prompts documentados | Arquivos dos prompts, objetivo, entradas e saída esperada |
| Respostas validadas | Testes de contrato, grounding e rejeição de saída inválida |
| Erros e timeouts tratados | Testes de falha, timeout e fallback com serviços simulados |
| Agentes e ferramentas definidos | Registro dos agentes, ferramentas, permissões e contratos |
| RAG com contexto relevante | Casos com contexto pertinente, irrelevante e ausente |
| Sem credenciais expostas | Gitleaks aprovado e uso de variáveis de ambiente |
| Alterações documentadas na PR | Resumo, motivo, impacto e resultados de validação |
| Tipos de fonte válidos | Tipos de fonte do RAG aceitos e rejeitados e testes correspondentes |

“Tipos de fonte” foi interpretado como fontes de dados do RAG. A lista de tipos
permitidos deverá acompanhar a implementação; este CI não inventa uma allowlist.
Para uma alteração sem impacto em um item, registre a justificativa na PR antes
de marcá-lo. A validade das evidências e justificativas depende da revisão humana.

## Verificações automáticas

- Gitleaks v8.24.2 varre o histórico Git disponível, com saída redigida. Não exige
  licença da action comercial nem credenciais de provedores de IA.
- Os testes do próprio validador são executados com Python 3.13.
- Quando houver Python em `app/`, `config/`, `interfaces/` ou `src/`, instala as
  dependências de `requirements.txt` ou `pyproject.toml` e executa unittest em
  `tests/`. Falha se não houver dependências declaradas ou testes descobertos.
  Subpastas de testes da aplicação devem conter `__init__.py` para descoberta no
  Python 3.13. `tests/ci/` não contém esse arquivo para não contar como teste da IA.
- Enquanto não houver implementação, o resumo informa que os testes da IA não
  foram executados. Na criação deste CI, havia somente README e licença.

Não há chamadas a LLMs, bancos ou serviços externos nos testes do CI. Os testes
futuros da aplicação devem usar mocks/fixtures. O job termina após 15 minutos.

## Proteção de merge

Falhar um check não bloqueia o merge por si só. Em Settings → Rules → Rulesets,
um administrador pode exigir os nove checks e os jobs Gitleaks/Python para main.
Este workflow não altera as regras de proteção do repositório.

## Validação local

```bash
python -m unittest discover -s tests/ci -v
```
